"""Regenerate docs/screenshots/*.png (README + landing page) from a real running console.

    docker compose up -d rabbitmq
    python scripts/screenshots.py            # needs `pip install playwright` + chromium

Seeds the demo dead-letter queues (app.demo, idempotent), starts its own QueueLens with
auth on and a throwaway admin password, creates some history through the API (alert
rules, parks, a replay, a delete) and captures every screen at 1440x900.
"""

import asyncio
import contextlib
import os
import secrets
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.demo import seed  # noqa: E402

AMQP = os.environ.get("QUEUELENS_RABBITMQ_URL", "amqp://queuelens:queuelens@localhost:5672/")
MGMT = os.environ.get("QUEUELENS_RABBITMQ_MANAGEMENT_URL", "http://localhost:15672")
OUT = Path(os.environ.get("SCREENSHOT_DIR", ROOT / "docs" / "screenshots"))
PORT = 8150


@contextlib.contextmanager
def server(workdir: str) -> Iterator[tuple[str, tuple[str, str]]]:
    auth = ("admin", secrets.token_urlsafe(12))
    env = {
        **os.environ,
        "QUEUELENS_AUTH_ENABLED": "true",
        "QUEUELENS_ADMIN_USERNAME": auth[0],
        "QUEUELENS_ADMIN_PASSWORD": auth[1],
        "QUEUELENS_RABBITMQ_URL": AMQP,
        "QUEUELENS_RABBITMQ_MANAGEMENT_URL": MGMT,
        "QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME": "queuelens",
        "QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD": "queuelens",
        "QUEUELENS_DATABASE_URL": f"sqlite+aiosqlite:///{workdir}/screens.db",
        "QUEUELENS_ALERT_INTERVAL_SECONDS": "2",
        "QUEUELENS_ENVIRONMENTS_JSON": '{"staging": {"vhosts": ["/", "staging"]}, '
                                       '"production": {"vhosts": ["/", "payments"]}}',
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{PORT}"
    try:
        for _ in range(60):
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(f"{base}/ready").status_code == 200:
                    break
            time.sleep(0.5)
        yield base, auth
    finally:
        proc.terminate()
        proc.wait(10)


def prepare(base: str, auth: tuple[str, str]) -> None:
    """History worth showing: alert rules that fire, and a few recovery actions."""
    api = httpx.Client(base_url=base, auth=auth, timeout=30)
    for rule in (
        {"name": "DLQ backlog critical", "pattern": "*.dlq", "metric": "messages_ready",
         "operator": ">", "threshold": 100, "severity": "Alert", "channels": ["slack"]},
        {"name": "Orders DLQ growing", "pattern": "orders.*.dlq", "metric": "messages_ready",
         "operator": ">", "threshold": 20, "severity": "Warning", "channels": ["email"]},
        {"name": "Payments worker down", "pattern": "payments.retry", "metric": "consumers",
         "operator": "=", "threshold": 0, "severity": "Warning", "channels": []},
    ):
        api.post("/api/alerts", json=rule).raise_for_status()

    def pick(queue: str, positions: list[int]) -> tuple[str, list[str]]:
        page = api.get(f"/api/queues/{queue}/messages?snapshot=new&limit=50").json()
        return page["snapshot"]["id"], [page["messages"][i]["fingerprint"] for i in positions]

    # a replay policy with a real run: it parks the orders that died 3 or 5 times
    policy = api.post("/api/policies", json={
        "name": "orders retry", "queue": "orders.created.dlq", "max_deaths": 3,
        "backoff_minutes": 5, "interval_minutes": 10, "cap": 100}).json()
    api.post(f"/api/policies/{policy['id']}/run").raise_for_status()
    sid, fps = pick("payments.retry.dlq", [10, 11, 12])
    for fp in fps:
        api.post("/api/messages/park", json={"source_queue": "payments.retry.dlq",
                                             "fingerprint": fp, "snapshot": sid, "confirm": True})
    sid, fps = pick("orders.created.dlq", [0, 1])
    for fp in fps:
        api.post("/api/messages/replay", json={
            "source_queue": "orders.created.dlq", "fingerprint": fp, "mode": "copy",
            "target": {"type": "queue", "queue": "orders.created"}, "snapshot": sid,
            "confirm": True})
    sid, fps = pick("inventory.sync.dlq", [2])
    api.post("/api/messages/delete", json={"source_queue": "inventory.sync.dlq",
                                           "fingerprint": fps[0], "snapshot": sid, "confirm": True})
    for _ in range(40):  # alert rules evaluate every 2 s here
        if len(api.get("/api/notifications").json()["notifications"]) >= 3:
            break
        time.sleep(0.5)
    # the Management API reports counts a few seconds late (quorum queues later still):
    # capture only once two reads in a row agree and every queue has its stats
    previous = None
    for _ in range(30):
        current = {q["name"]: q.get("messages") for q in httpx.get(
            f"{MGMT}/api/queues", auth=("queuelens", "queuelens")).json()}
        if None not in current.values() and current == previous:
            break
        previous = current
        time.sleep(2)


def capture(base: str, auth: tuple[str, str]) -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:  # noqa: BLE001 - no bundled build for this playwright: use Chrome
            browser = p.chromium.launch(channel="chrome")

        def open_console(theme: str = "light"):  # noqa: ANN202
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, device_scale_factor=1,
                http_credentials={"username": auth[0], "password": auth[1]},
            )
            context.add_init_script(f"localStorage.setItem('ql_theme', '{theme}')")
            page = context.new_page()
            page.goto(f"{base}/app")
            page.wait_for_selector("text=DLQ Recovery Dashboard")
            page.wait_for_timeout(800)
            return page

        def shot(page, name: str) -> None:  # noqa: ANN001
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / name))
            print("captured", name)

        def nav(page, label: str, ready: str) -> None:  # noqa: ANN001
            page.get_by_role("button", name=label, exact=True).first.click()
            page.wait_for_selector(f"text={ready}")

        page = open_console()
        shot(page, "dashboard.png")
        page.get_by_role("button", name="Notifications").click()
        page.wait_for_selector("text=View all notifications")
        shot(page, "notifications.png")
        page.keyboard.press("Escape")
        page.reload()
        page.wait_for_selector("text=DLQ Recovery Dashboard")
        nav(page, "Alerts", "DLQ backlog critical")
        shot(page, "alerts.png")
        nav(page, "Audit Log", "Audit Log")
        shot(page, "audit.png")
        nav(page, "Topology", "Topology")
        shot(page, "topology.png")
        nav(page, "Replay Policies", "orders retry")
        shot(page, "policies.png")
        nav(page, "Configuration", "Broker Connection")
        page.wait_for_selector("text=Connection successful")
        shot(page, "configuration.png")

        nav(page, "Queues", "payments.retry.dlq")
        page.get_by_role("link", name="payments.retry.dlq", exact=True).first.click()
        page.get_by_role("button", name="Browse Messages").click()
        page.wait_for_selector("text=Snapshot of the")
        page.get_by_text("pay-0003", exact=True).first.click()  # died five times, gzip body
        shot(page, "message-detail.png")
        page.get_by_role("button", name="Replay (Move)").last.click()
        page.wait_for_selector("text=Select Replay Action")
        shot(page, "replay-wizard.png")

        shot(open_console("dark"), "dashboard-dark.png")
        browser.close()


def main() -> None:
    asyncio.run(seed(AMQP))
    with tempfile.TemporaryDirectory() as workdir, server(workdir) as (base, auth):
        prepare(base, auth)
        capture(base, auth)


if __name__ == "__main__":
    main()
