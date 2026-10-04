"""Test the Prometheus/Alertmanager examples in deploy/ (docs/ALERTING.md), all in Docker:

1. rules     promtool checks deploy/prometheus/prometheus.yml and runs alerts.test.yml
             against alerts.yml
2. routing   amtool checks deploy/alertmanager/alertmanager.yml and where each rule's
             severity is routed
3. delivery  Alertmanager runs that config with its URL files pointing at a local sink;
             alerts posted to /api/v2/alerts must arrive as Slack and webhook payloads,
             firing and then resolved

    python scripts/test_alerting.py          # stdlib only; needs Docker
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NoReturn

PROMETHEUS = "prom/prometheus:v3.15.0"
ALERTMANAGER = "prom/alertmanager:v0.34.1"
DEPLOY = Path(__file__).resolve().parents[1] / "deploy"

# receivers each severity must reach, in amtool's order ("" = no severity label)
ROUTES = {"critical": "webhook,slack", "warning": "slack", "": "slack"}

# What Prometheus sends for each rule in alerts.yml: rule, extra labels, rendered summary and
# description. One DLQ name in two vhosts on one rule: group_by must keep them apart.
Alert = tuple[str, dict[str, str], str, str]
ALERTS: list[Alert] = [
    ("QueueLensBrokerDown", {}, "QueueLens lost its RabbitMQ connection",
     "queuelens_rabbitmq_ready has been 0 for 5 minutes."),
    ("DLQAboveThreshold", {"environment": "development", "vhost": "/", "queue": "orders.dlq"},
     "DLQ orders.dlq in development · / holds more than 1000 messages",
     "orders.dlq has 1500 dead letters."),
    ("DLQAboveThreshold", {"environment": "staging", "vhost": "ql-staging", "queue": "orders.dlq"},
     "DLQ orders.dlq in staging · ql-staging holds more than 1000 messages",
     "orders.dlq has 1200 dead letters."),
    ("DLQGrowing", {"environment": "development", "vhost": "/", "queue": "payments.dlq"},
     "DLQ payments.dlq in development · / is growing fast",
     "payments.dlq grew by 300 messages in 30 minutes."),
    ("QueueLensScopeUnreachable", {"environment": "staging", "vhost": "ql-staging"},
     "QueueLens can't read staging · ql-staging",
     "Its DLQs aren't monitored: check that broker's Management API and the credentials "
     "QueueLens uses."),
    ("QueueLensActionFailures", {"action": "replay", "result": "failed"},
     "QueueLens actions are failing",
     "15 failed actions in the last 15 minutes — check the audit log."),
    ("ReplayPolicyPaused", {"policy": "orders"}, "Replay policy orders paused itself",
     "orders stopped after failed runs: check the audit log, then re-enable it."),
]
BROKER_DOWN = ALERTS[0]


def fail(message: str) -> NoReturn:
    sys.exit(f"FAIL: {message}")


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def severities() -> dict[str, str]:
    """alertname -> severity label, from the real rules file."""
    text = (DEPLOY / "prometheus" / "alerts.yml").read_text()
    found = dict(re.findall(r"- alert: (\w+)\n(?:.*\n)*?\s+severity: (\w+)", text))
    names = {name for name, *_ in ALERTS}
    if set(found) != names:
        fail(f"alerts.yml has rules {sorted(found)}; ALERTS in this script has {sorted(names)}")
    return found


def labels(entry: Alert, severity: dict[str, str]) -> dict[str, str]:
    name, extra, _, _ = entry
    return {"alertname": name, "severity": severity[name], "job": "queuelens",
            "instance": "queuelens:8000", **extra}


def test_rules() -> None:
    print("== rules: promtool check config, test rules")
    for args in (["check", "config", "prometheus.yml"], ["test", "rules", "alerts.test.yml"]):
        run = docker("run", "--rm", "-v", f"{DEPLOY / 'prometheus'}:/rules:ro", "-w", "/rules",
                     "--entrypoint", "promtool", PROMETHEUS, *args)
        if run.returncode:
            fail(f"promtool {' '.join(args)}\n{run.stdout}{run.stderr}")
        print(f"  {' '.join(args)}: ok")


def amtool(*args: str) -> None:
    run = docker("run", "--rm", "-v", f"{DEPLOY / 'alertmanager'}:/cfg:ro",
                 "--entrypoint", "amtool", ALERTMANAGER, *args)
    if run.returncode:
        fail(f"amtool {' '.join(args)}\n{run.stdout}{run.stderr}")


def test_routing(severity: dict[str, str]) -> None:
    print("== routing: amtool check-config, config routes test")
    amtool("check-config", "/cfg/alertmanager.yml")
    print("  check-config: ok")
    cases = [labels(entry, severity) for entry in ALERTS] + [{"alertname": "NoSeverity"}]
    for case in cases:
        want = ROUTES[case.get("severity", "")]
        amtool("config", "routes", "test", "--config.file=/cfg/alertmanager.yml",
               f"--verify.receivers={want}", *(f"{key}={value}" for key, value in case.items()))
        shown = " ".join(f"{key}={case[key]}" for key in ("alertname", "severity", "queue")
                         if key in case)
        print(f"  {shown} -> {want}")


class Sink(BaseHTTPRequestHandler):
    """Records every POST; answers like Slack does (200 "ok"), which webhooks accept too."""

    received: list[tuple[str, dict[str, Any]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.received.append((self.path, json.loads(body)))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: Any) -> None:
        pass


def posts(path: str) -> list[dict[str, Any]]:
    return [payload for got, payload in Sink.received if got == path]


def wait_for(what: str, condition: Callable[[], bool], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            fail(f"timed out waiting for {what}; received {Sink.received}")
        time.sleep(0.2)


def rfc3339(seconds_from_now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds_from_now))


def post_alerts(base: str, severity: dict[str, str], entries: list[Alert], **times: str) -> None:
    alerts = [
        {"labels": labels(entry, severity),
         "annotations": {"summary": entry[2], "description": entry[3]},
         "generatorURL": "http://prometheus:9090/graph", **times}
        for entry in entries
    ]
    request = urllib.request.Request(f"{base}/api/v2/alerts", json.dumps(alerts).encode(),
                                     {"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=5).close()


def check_slack(payload: dict[str, Any], title: str, text: str, color: str) -> None:
    attachment = payload["attachments"][0]
    got = (attachment["title"], attachment["text"].strip(), attachment["color"])
    if got != (title, text, color):
        fail(f"Slack message {got}, expected {(title, text, color)}")
    print(f"  slack    {title}")


def check_webhook(payload: dict[str, Any], status: str, want: dict[str, str]) -> None:
    got = (payload["version"], payload["status"], [a["labels"] for a in payload["alerts"]])
    if got != ("4", status, [want]):
        fail(f"webhook payload {payload}")
    print(f"  webhook  {status} {want['alertname']}")


def test_delivery(severity: dict[str, str]) -> None:
    print("== delivery: Alertmanager -> Slack + webhook sink")
    sink = ThreadingHTTPServer(("0.0.0.0", 0), Sink)  # the container reaches it via the host
    threading.Thread(target=sink.serve_forever, daemon=True).start()
    sink_url = f"http://host.docker.internal:{sink.server_address[1]}"

    workdir = Path(tempfile.mkdtemp(prefix="queuelens-alerting-"))
    config = (DEPLOY / "alertmanager" / "alertmanager.yml").read_text()
    for key in ("group_wait", "group_interval"):  # seconds, not minutes
        config, count = re.subn(rf"^(\s*{key}:) \S+", r"\1 1s", config, flags=re.M)
        if count != 1:
            fail(f"expected one {key} in alertmanager.yml, found {count}")
    (workdir / "alertmanager.yml").write_text(config)
    (workdir / "slack-webhook-url").write_text(f"{sink_url}/slack\n")
    (workdir / "webhook-url").write_text(f"{sink_url}/webhook\n")
    workdir.chmod(0o755)  # the image runs as nobody
    for file in workdir.iterdir():
        file.chmod(0o644)

    name = f"queuelens-alerting-test-{os.getpid()}"
    try:
        start = docker("run", "-d", "--name", name, "--add-host=host.docker.internal:host-gateway",
                       "-p", "127.0.0.1::9093", "-v", f"{workdir}:/etc/alertmanager:ro",
                       ALERTMANAGER, "--config.file=/etc/alertmanager/alertmanager.yml",
                       "--storage.path=/alertmanager", "--cluster.listen-address=")
        if start.returncode:
            fail(f"docker run: {start.stderr}")
        base = "http://" + docker("port", name, "9093/tcp").stdout.split()[0]

        def ready() -> bool:
            try:
                with urllib.request.urlopen(f"{base}/-/ready", timeout=2) as response:
                    return bool(response.status == 200)
            except OSError:
                return False

        wait_for("Alertmanager to start", ready)

        started = rfc3339(-60)
        post_alerts(base, severity, ALERTS, startsAt=started)
        wait_for("a Slack message per alert and one webhook call",
                 lambda: len(posts("/slack")) >= len(ALERTS) and len(posts("/webhook")) >= 1)
        time.sleep(3)  # anything else routed would have arrived by now
        if len(posts("/slack")) != len(ALERTS) or len(posts("/webhook")) != 1:
            fail(f"expected {len(ALERTS)} Slack messages and 1 webhook call: {Sink.received}")
        by_title = {p["attachments"][0]["title"]: p for p in posts("/slack")}
        for entry in ALERTS:
            title = f"[FIRING:1] {entry[2]}"
            if title not in by_title:
                fail(f"no Slack message titled {title!r}: {sorted(by_title)}")
            color = "danger" if severity[entry[0]] == "critical" else "warning"
            check_slack(by_title[title], title, entry[3], color)
        check_webhook(posts("/webhook")[0], "firing", labels(BROKER_DOWN, severity))

        post_alerts(base, severity, [BROKER_DOWN], startsAt=started, endsAt=rfc3339(-1))
        wait_for("the resolved notifications",
                 lambda: len(posts("/slack")) > len(ALERTS) and len(posts("/webhook")) > 1)
        check_slack(posts("/slack")[-1], f"[RESOLVED] {BROKER_DOWN[2]}",
                    f"Resolved: {BROKER_DOWN[3]}", "good")
        check_webhook(posts("/webhook")[-1], "resolved", labels(BROKER_DOWN, severity))
    except BaseException:
        print(docker("logs", name).stderr[-4000:], file=sys.stderr)
        raise
    finally:
        docker("rm", "-f", name)
        sink.shutdown()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    severity = severities()
    test_rules()
    test_routing(severity)
    test_delivery(severity)
    print("alerting: all checks passed")
