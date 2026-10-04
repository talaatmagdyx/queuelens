"""QueueLens live acceptance run: a real RabbitMQ, a real QueueLens process, black-box HTTP.

Boots QueueLens itself (as a subprocess, auth on), seeds a real broker, and checks every
feature group end to end — including the failure modes the mocked suite can't see
(broker returns, delivery limits, Management API stats lag, restarts). Each check maps to
a numbered feature group. Statuses:
  PASS  claim holds        FAIL  claim broken / defect confirmed
  LIMIT documented limitation confirmed     NOTE  observation worth knowing
Exits non-zero on any FAIL or ERROR.

Needs a DISPOSABLE broker — it deletes `t<N>.*` queues, the `t4.ex`/`t8.ex` exchanges
and the `ql-staging`/`t10-rogue-vhost` vhosts — plus two Mailpit instances (plain SMTP,
and STARTTLS-only with an untrusted certificate). Locally:

    docker compose up -d rabbitmq
    docker run -d --rm -p 1025:1025 -p 8025:8025 axllent/mailpit
    openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=untrusted.invalid \
        -keyout /tmp/ql-key.pem -out /tmp/ql-cert.pem && chmod 644 /tmp/ql-*.pem
    docker run -d --rm -p 1026:1025 -p 8027:8025 -v /tmp:/certs:ro axllent/mailpit \
        --smtp-tls-cert /certs/ql-cert.pem --smtp-tls-key /certs/ql-key.pem --smtp-require-starttls
    ACCEPTANCE=1 python tests/acceptance/run.py

Endpoints are overridable with ACCEPTANCE_* env vars (see below); CI runs it as the
`acceptance` job. ACCEPTANCE_DATABASE_URL (postgresql+asyncpg://...) runs it on a
DISPOSABLE PostgreSQL database instead of a SQLite file: its QueueLens tables are dropped.
"""

import asyncio
import base64
import gzip
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import quote

import aio_pika
import httpx
from cryptography.fernet import Fernet

REPO = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
ENV = os.environ.get
WORK = ENV("ACCEPTANCE_WORKDIR") or tempfile.mkdtemp(prefix="ql-acceptance-")

PORT = int(ENV("ACCEPTANCE_PORT", "8124"))
BASE = f"http://127.0.0.1:{PORT}"
BROKER = ENV("ACCEPTANCE_BROKER_HOST", "127.0.0.1")
AMQP_PORT = int(ENV("ACCEPTANCE_AMQP_PORT", "5672"))
MGMT = f"http://{BROKER}:{ENV('ACCEPTANCE_MGMT_PORT', '15672')}"
# the compose/CI broker's user doubles as its password; override for anything else
MAUTH = (ENV("ACCEPTANCE_BROKER_USER", "queuelens"),
         ENV("ACCEPTANCE_BROKER_PASSWORD") or ENV("ACCEPTANCE_BROKER_USER", "queuelens"))


def amqp_url(vhost: str = "") -> str:
    return f"amqp://{MAUTH[0]}:{MAUTH[1]}@{BROKER}:{AMQP_PORT}/{vhost}"


AMQP = amqp_url()
SMTP_HOST = ENV("ACCEPTANCE_SMTP_HOST", "127.0.0.1")
SMTP_PORT = int(ENV("ACCEPTANCE_SMTP_PORT", "1025"))
SMTP_TLS_PORT = int(ENV("ACCEPTANCE_SMTP_TLS_PORT", "1026"))
MAILPIT = ENV("ACCEPTANCE_MAILPIT_URL", "http://127.0.0.1:8025")
MAILPIT_TLS = ENV("ACCEPTANCE_MAILPIT_TLS_URL", "http://127.0.0.1:8027")
DB = f"{WORK}/acceptance.db"
PG = ENV("ACCEPTANCE_DATABASE_URL")
DATABASE_URL = PG or f"sqlite+aiosqlite:///{DB}"
LOG = f"{WORK}/server.log"
RESULTS_FILE = ENV("ACCEPTANCE_RESULTS", f"{WORK}/acceptance-results.json")
# every credential and sample secret is generated per run — nothing secret-looking in git
ADMIN = ("admin", token_urlsafe(12))
OPSENV = ("opsenv", token_urlsafe(12))
SECRET = token_urlsafe(9)  # sample sensitive value carried in payloads/headers to mask
# looked up by name: a credential-shaped constant next to a username reads as a literal
PW = {"broken": token_urlsafe(6)}
KEY = Fernet.generate_key().decode()
HOOK_PORT = int(ENV("ACCEPTANCE_HOOK_PORT", "8999"))
HOOK = f"http://127.0.0.1:{HOOK_PORT}"
USERS: dict[str, tuple[str, str]] = {}
NEW_VIEWER_PW = token_urlsafe(12)
SMTP_SECRET, SLACK_SECRET, PD_SECRET = (token_urlsafe(9) for _ in range(3))

RESULTS: list[dict] = []


def _redact(text: str) -> str:
    """Evidence is printed to CI logs and saved as an artifact — never with this run's
    generated credentials or sample secrets in it."""
    for secret in (ADMIN[1], OPSENV[1], SECRET, NEW_VIEWER_PW, SMTP_SECRET, SLACK_SECRET,
                   PD_SECRET, PW["broken"], KEY,
                   *(password for _, password in USERS.values())):
        text = text.replace(secret, "***")
    return text


def rec(group, name, status, evidence=""):
    evidence = _redact(str(evidence))
    RESULTS.append({"group": group, "name": name, "status": status, "evidence": evidence[:700]})
    print(f"[{status:5}] G{group:<2} {name} :: {evidence[:170]}", flush=True)


def check(group, name, cond, evidence="", bad="FAIL"):
    rec(group, name, "PASS" if cond else bad, evidence)
    return bool(cond)


# ------------------------------------------------------------------ webhook receiver
HOOKS: list[dict] = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = body.decode(errors="replace")
        HOOKS.append({"path": self.path, "json": parsed, "t": time.monotonic()})
        self.send_response(500 if self.path.startswith("/fail") else 200)
        self.end_headers()

    def log_message(self, *_a):
        pass


def start_hook_server():
    srv = ThreadingHTTPServer(("127.0.0.1", HOOK_PORT), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


# ------------------------------------------------------------------ server lifecycle
SERVER: subprocess.Popen | None = None


def server_env(**over):
    env = dict(os.environ)
    env.update(
        {
            "QUEUELENS_AUTH_ENABLED": "true",
            "QUEUELENS_ADMIN_USERNAME": ADMIN[0],
            "QUEUELENS_ADMIN_PASSWORD": ADMIN[1],
            "QUEUELENS_USERS_JSON": json.dumps({OPSENV[0]: OPSENV[1]}),
            "QUEUELENS_RABBITMQ_URL": AMQP,
            "QUEUELENS_RABBITMQ_MANAGEMENT_URL": MGMT,
            "QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME": MAUTH[0],
            "QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD": MAUTH[1],
            "QUEUELENS_DATABASE_URL": DATABASE_URL,
            "QUEUELENS_MAX_PREVIEW_MESSAGES": "10",
            "QUEUELENS_MAX_MESSAGE_SIZE_BYTES": "4096",
            "QUEUELENS_REFETCH_WINDOW_SIZE": "60",
            "QUEUELENS_MAX_BULK_SIZE": "20",
            "QUEUELENS_BULK_DRY_RUN_TTL_SECONDS": "30",
            "QUEUELENS_REPLAY_TARGETS_JSON": json.dumps(
                {"t4.cfg.dlq": {"type": "queue", "queue": "t4.cfg.target"}}
            ),
            "QUEUELENS_ENVIRONMENTS_JSON": json.dumps(
                {
                    "staging": {"vhosts": ["/", "ql-staging"]},
                    "broken": {
                        "rabbitmq_url": f"amqp://nobody:{PW["broken"]}@{BROKER}:{AMQP_PORT}/",
                        "management_username": "nobody",
                        "management_password": PW["broken"],
                    },
                }
            ),
            "QUEUELENS_SMTP_HOST": SMTP_HOST,
            "QUEUELENS_SMTP_PORT": str(SMTP_PORT),
            "QUEUELENS_ALERT_INTERVAL_SECONDS": "1",
            "QUEUELENS_SECRET_KEY": KEY,
        }
    )
    env.update(over)
    return env


def start_server(timeout=30, **over) -> bool:
    global SERVER
    log = open(LOG, "a")
    log.write(f"\n===== start {datetime.now().isoformat()} =====\n")
    log.flush()
    SERVER = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        cwd=REPO, env=server_env(**over), stdout=log, stderr=subprocess.STDOUT,
    )
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if SERVER.poll() is not None:
            return False
        try:
            if httpx.get(f"{BASE}/ready", timeout=1).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def stop_server():
    global SERVER
    if SERVER and SERVER.poll() is None:
        SERVER.terminate()
        try:
            SERVER.wait(10)
        except subprocess.TimeoutExpired:
            SERVER.kill()
    SERVER = None


# ------------------------------------------------------------------ helpers
CLIENT: httpx.AsyncClient
CONN: aio_pika.abc.AbstractRobustConnection


async def api(method, path, auth=ADMIN, **kw) -> httpx.Response:
    return await CLIENT.request(method, path, auth=auth, **kw)


async def until(fn, timeout=20.0, interval=0.5):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = await fn()
        if last:
            return last
        await asyncio.sleep(interval)
    return last


async def decl(name, args=None, durable=True, conn=None):
    async with (conn or CONN).channel() as ch:
        await ch.declare_queue(name, durable=durable, arguments=args)


async def decl_ex(name, kind="direct"):
    async with CONN.channel() as ch:
        await ch.declare_exchange(name, kind, durable=True)


async def bind(q, ex, rk):
    async with CONN.channel() as ch:
        queue = await ch.declare_queue(q, passive=True)
        await queue.bind(ex, rk)


def body_of(payload) -> bytes:
    return payload if isinstance(payload, bytes) else json.dumps(payload).encode()


async def pub(rk, payload, exchange="", conn=None, **props):
    async with (conn or CONN).channel() as ch:
        ex = ch.default_exchange if exchange == "" else await ch.get_exchange(exchange)
        await ex.publish(aio_pika.Message(body=body_of(payload), **props), routing_key=rk)


async def count(name, conn=None) -> int | None:
    ch = await (conn or CONN).channel()
    try:
        q = await ch.declare_queue(name, passive=True)
        return q.declaration_result.message_count
    except Exception:  # noqa: BLE001
        return None
    finally:
        if not ch.is_closed:
            await ch.close()


async def drain(name, conn=None) -> list:
    out = []
    async with (conn or CONN).channel() as ch:
        q = await ch.declare_queue(name, passive=True)
        while (m := await q.get(no_ack=True, fail=False)) is not None:
            out.append(m)
    return out


async def deadletter(work_queue, n, conn=None):
    async with (conn or CONN).channel() as ch:
        q = await ch.declare_queue(work_queue, passive=True)
        for _ in range(n):
            m = await q.get(no_ack=False, fail=False)
            if m is None:
                break
            await m.reject(requeue=False)


async def preview(q, limit=None, auth=ADMIN):
    params = {"limit": limit} if limit else None
    r = await api("GET", f"/api/queues/{quote(q, safe='')}/messages", auth=auth, params=params)
    return r.json().get("messages", []) if r.status_code == 200 else r


async def ql_count(q):
    r = await api("GET", f"/api/queues/{quote(q, safe='')}")
    return r.json()["queue"]["messages"] if r.status_code == 200 else None


async def wait_ql_count(q, n, timeout=20):
    """Management API counts lag the broker by the stats interval (~5s)."""
    return await until(lambda: _eq(ql_count(q), n), timeout)


async def _eq(coro, n):
    return (await coro) == n


async def audit(**params):
    r = await api("GET", "/api/audit", params={"limit": 500, **params})
    return r.json()["events"]


async def notifications():
    return (await api("GET", "/api/notifications")).json()["notifications"]


async def put_settings(values, auth=ADMIN):
    return await api("PUT", "/api/settings", auth=auth, json={"values": values})


async def mailpit_messages(base=MAILPIT):
    async with httpx.AsyncClient() as c:
        return (await c.get(f"{base}/api/v1/messages")).json().get("messages", [])


async def mailpit_text(msg_id, base=MAILPIT):
    async with httpx.AsyncClient() as c:
        return (await c.get(f"{base}/api/v1/message/{msg_id}")).json().get("Text", "")


def _pg(sql, fetch):
    import asyncpg  # the app's own driver

    async def run():
        con = await asyncpg.connect(PG.replace("+asyncpg", ""))
        try:
            return await (con.fetch(sql) if fetch else con.execute(sql))
        finally:
            await con.close()

    with ThreadPoolExecutor(1) as pool:  # called from inside the running event loop
        return pool.submit(lambda: asyncio.run(run())).result()


def db_rows(sql):
    if PG:
        return _pg(sql, fetch=True)
    con = sqlite3.connect(DB, timeout=10)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def db_exec(sql):
    if PG:
        return _pg(sql, fetch=False)
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.execute(sql)
        con.commit()
    finally:
        con.close()


def stored_bytes():
    """Everything the database holds, to grep for a plaintext secret."""
    if not PG:
        return open(DB, "rb").read()
    tables = [t for (t,) in db_rows("select tablename from pg_tables where schemaname = 'public'")]
    return "".join(str(row[0]) for t in tables for row in db_rows(f"select x::text from {t} x")).encode()


def by_payload(messages, key, value):
    return next(
        (m for m in messages if isinstance(m.get("payload"), dict) and m["payload"].get(key) == value),
        None,
    )


# ------------------------------------------------------------------ cleanup
async def cleanup():
    async with httpx.AsyncClient(base_url=MGMT, auth=MAUTH, timeout=15) as m:
        for vh in ("/",):
            r = await m.get(f"/api/queues/{quote(vh, safe='')}")
            for q in r.json():
                if re.match(r"^(t\d+\.|=)", q["name"]):
                    await m.delete(f"/api/queues/{quote(vh, safe='')}/{quote(q['name'], safe='')}")
        for ex in ("t4.ex", "t8.ex"):
            await m.delete(f"/api/exchanges/%2F/{ex}")
        for vh in ("ql-staging", "t10-rogue-vhost"):
            await m.delete(f"/api/vhosts/{vh}")
    async with httpx.AsyncClient() as c:
        await c.delete(f"{MAILPIT}/api/v1/messages")
        await c.delete(f"{MAILPIT_TLS}/api/v1/messages")
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.exists(DB + suffix):
            os.remove(DB + suffix)
    if PG:
        from app.infrastructure.persistence.database import Database
        from app.infrastructure.persistence.models import Base

        database = Database(PG)
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.close()
    open(LOG, "w").close()


# ================================================================== G24 REST API
async def g24():
    for path in ("/docs", "/redoc", "/openapi.json"):
        anonymous = (await CLIENT.get(path)).status_code
        signed_in = (await api("GET", path)).status_code
        check(24, f"{path} served behind auth", (anonymous, signed_in) == (401, 200), (anonymous, signed_in))
    paths = (await api("GET", "/openapi.json")).json()["paths"]
    check(24, "OpenAPI schema covers the operational API", len(paths) >= 40, f"{len(paths)} paths")
    r = await api("POST", "/api/messages/replay", json={"source_queue": "x", "fingerprint": "short"})
    check(24, "Server-side validation (pydantic → 422)", r.status_code == 422, r.status_code)


# ================================================================== G12 auth & RBAC
async def g12():
    r = await CLIENT.get("/api/queues")
    check(12, "No credentials → 401 + WWW-Authenticate: Basic",
          r.status_code == 401 and r.headers.get("www-authenticate") == "Basic", r.status_code)
    r = await api("GET", "/api/queues", auth=("admin", token_urlsafe(6)))
    check(12, "Wrong password → 401", r.status_code == 401, r.status_code)
    for path in ("/health", "/ready"):
        check(12, f"{path} is public", (await CLIENT.get(path)).status_code == 200)
    for path in ("/metrics", "/app"):
        check(12, f"{path} requires auth", (await CLIENT.get(path)).status_code == 401)

    for name, role in (("viewer1", "Viewer"), ("oper1", "Operator"), ("admin2", "Admin")):
        r = await api("POST", "/api/users/invite", json={"username": name, "role": role,
                                                          "email": f"{name}@test.local"})
        ok = r.status_code == 200 and r.json().get("password")
        check(12, f"Admin invites {role} '{name}' (one-time password returned)", ok, r.status_code)
        if ok:
            USERS[name] = (name, r.json()["password"])
    # one-time passwords: nothing but /api/me and the password change answers until replaced
    one_time = USERS["oper1"]
    blocked = await api("GET", "/api/queues", auth=one_time)
    me = (await api("GET", "/api/me", auth=one_time)).json()
    check(12, "Invited account must replace its one-time password first (403 elsewhere)",
          blocked.status_code == 403 and me.get("must_change_password") is True,
          (blocked.status_code, me))
    for name, (_, first) in list(USERS.items()):
        pw = {"old": first, "new": token_urlsafe(12)}
        r = await api("POST", "/api/users/me/password", auth=(name, pw["old"]),
                      json={"current_password": pw["old"], "new_password": pw["new"]})
        if r.status_code == 200:
            USERS[name] = (name, pw["new"])
    check(12, "After the change the account works, the one-time password doesn't",
          (await api("GET", "/api/queues", auth=USERS["oper1"])).status_code == 200
          and (await api("GET", "/api/me", auth=one_time)).status_code == 401)
    r = await api("POST", "/api/users/invite", json={"username": "viewer1", "role": "Viewer"})
    check(12, "Duplicate invite → 409", r.status_code == 409, r.status_code)
    r = await api("POST", "/api/users/invite", json={"username": "bad name!", "role": "Viewer"})
    check(12, "Invalid username → 422", r.status_code == 422, r.status_code)
    r = await api("POST", "/api/users/invite", auth=OPSENV, json={"username": "x1", "role": "Admin"})
    check(12, "Operator cannot invite (403)", r.status_code == 403, r.status_code)

    roles = {}
    for label, auth in (("admin", ADMIN), ("opsenv", OPSENV), *[(n, USERS[n]) for n in USERS]):
        roles[label] = (await api("GET", "/api/me", auth=auth)).json().get("role")
    check(12, "/api/me resolves roles (env admin, env operator, DB users)",
          roles == {"admin": "Admin", "opsenv": "Operator", "viewer1": "Viewer",
                    "oper1": "Operator", "admin2": "Admin"}, roles)
    accounts = (await api("GET", "/api/users")).json()["accounts"]
    check(12, "/api/users lists accounts with roles", len(accounts) >= 5,
          [(a["username"], a["role"]) for a in accounts])

    msgs = await until(lambda: _mail_to("viewer1@test.local"), 10)
    if msgs:
        text = await mailpit_text(msgs[0]["ID"])
        check(12, "Invite email delivered via SMTP", "viewer1" in text, msgs[0]["Subject"])
        check(12, "Invite email never contains the password", USERS["viewer1"][1] not in text)
    else:
        rec(12, "Invite email delivered via SMTP", "FAIL", "no message in mailpit")

    viewer = USERS["viewer1"]
    reads = ["/api/queues", "/api/audit", "/api/topology", "/api/settings", "/api/alerts",
             "/api/notifications", "/api/environments", "/api/metrics/summary", "/metrics"]
    codes = {p: (await api("GET", p, auth=viewer)).status_code for p in reads}
    check(12, "Viewer can read every read-only surface", set(codes.values()) == {200}, codes)
    writes = [
        ("POST", "/api/messages/replay", {"source_queue": "q", "fingerprint": "a" * 64, "confirm": True}),
        ("POST", "/api/messages/park", {"source_queue": "q", "fingerprint": "a" * 64, "confirm": True}),
        ("POST", "/api/messages/delete", {"source_queue": "q", "fingerprint": "a" * 64, "confirm": True}),
        ("POST", "/api/messages/publish", {"routing_key": "q", "payload": "x", "confirm": True}),
        ("POST", "/api/messages/bulk/dry-run", {"source_queue": "q", "action": "park"}),
        ("POST", "/api/alerts", {"name": "x"}),
        ("POST", "/api/alerts/test-channel", {"channel": "webhook"}),
        ("POST", "/api/environments/activate", {"environment": "staging"}),
        ("PUT", "/api/settings", {"values": {}}),
    ]
    codes = {p: (await api(m, p, auth=viewer, json=b)).status_code for m, p, b in writes}
    check(12, "Viewer is refused every mutating endpoint (403)", set(codes.values()) == {403}, codes)
    oper = USERS["oper1"]
    codes = {
        "delete": (await api("POST", "/api/messages/delete", auth=oper, json=writes[2][2])).status_code,
        "settings": (await api("PUT", "/api/settings", auth=oper, json={"values": {}})).status_code,
        "env-create": (await api("POST", "/api/environments", auth=oper,
                                 json={"name": "x", "vhosts": ["/"]})).status_code,
        "invite": (await api("POST", "/api/users/invite", auth=oper,
                             json={"username": "zz", "role": "Viewer"})).status_code,
    }
    check(12, "Operator is refused Admin-only endpoints (403)", set(codes.values()) == {403}, codes)

    r = await api("POST", "/api/users/me/password", auth=viewer,
                  json={"current_password": token_urlsafe(6), "new_password": token_urlsafe(12)})
    check(12, "Password change: wrong current password → 403", r.status_code == 403, r.status_code)
    r = await api("POST", "/api/users/me/password", auth=viewer,
                  json={"current_password": viewer[1], "new_password": "short"})
    check(12, "Password change: < 10 chars → 422", r.status_code == 422, r.status_code)
    r = await api("POST", "/api/users/me/password", auth=viewer,
                  json={"current_password": viewer[1], "new_password": NEW_VIEWER_PW})
    old_status = (await api("GET", "/api/me", auth=viewer)).status_code
    USERS["viewer1"] = ("viewer1", NEW_VIEWER_PW)
    new_status = (await api("GET", "/api/me", auth=USERS["viewer1"])).status_code
    check(12, "Password change takes effect (old 401, new 200)",
          r.status_code == 200 and old_status == 401 and new_status == 200,
          (r.status_code, old_status, new_status))
    r = await api("POST", "/api/users/me/password",
                  json={"current_password": ADMIN[1], "new_password": "whatever-123"})
    check(12, "Env-managed account cannot change password via API (400)", r.status_code == 400)


async def _mail_to(addr, base=MAILPIT):
    return [m for m in await mailpit_messages(base) if any(t["Address"] == addr for t in m["To"])]


# ================================================================== G1 discovery
async def g1():
    await decl("t1.payments.work", {"x-dead-letter-exchange": "",
                                    "x-dead-letter-routing-key": "t1.payments.failed"})
    for q in ("t1.payments.failed", "t1.orders.dlq", "t1.billing_dlq", "t1.dead.letters", "t1.normal"):
        await decl(q)
    for i in range(3):
        await pub("t1.payments.work", {"i": i})
    await deadletter("t1.payments.work", 3)
    for i in range(5):
        await pub("t1.orders.dlq", {"i": i})
    await pub("t1.billing_dlq", {"i": 0})
    await wait_ql_count("t1.orders.dlq", 5)
    await wait_ql_count("t1.payments.failed", 3)

    queues = (await api("GET", "/api/queues", params={"dlq_only": "true"})).json()["queues"]
    names = {q["name"] for q in queues}
    check(1, "Name-convention detection (.dlq / _dlq / dead)",
          {"t1.orders.dlq", "t1.billing_dlq", "t1.dead.letters"} <= names, sorted(n for n in names if n.startswith("t1")))
    check(1, "Topology detection (queue another queue dead-letters into)", "t1.payments.failed" in names)
    check(1, "Non-DLQs excluded (DLX source, plain queue)",
          not ({"t1.payments.work", "t1.normal"} & names))
    counts = [q["messages"] for q in queues]
    check(1, "Risk-sorted (largest backlog first)", counts == sorted(counts, reverse=True), counts[:6])
    q = next(q for q in queues if q["name"] == "t1.orders.dlq")
    check(1, "Counts + status/severity per queue",
          q["messages"] == 5 and q["severity"] == "low" and q["status"] == "low" and q["kind"] == "dlq", q)
    check(1, "Single queue endpoint + 404 for unknown",
          (await api("GET", "/api/queues/t1.orders.dlq")).status_code == 200
          and (await api("GET", "/api/queues/t1.nope")).status_code == 404)

    # metadata-only: none of these may touch a message (a touched message comes back redelivered)
    TOPO["g1"] = time.monotonic()
    for path in ("/api/queues", "/api/queues?dlq_only=true", "/api/queues/t1.billing_dlq",
                 "/metrics", "/api/metrics/summary", "/api/topology", "/api/broker/test"):
        await api("GET", path)
    msgs = await drain("t1.billing_dlq")
    check(1, "Dashboard/metrics/topology polling never touches message bodies",
          msgs and not any(m.redelivered for m in msgs), [m.redelivered for m in msgs])
    await pub("t1.billing_dlq", {"i": 0})


# ================================================================== G2 inspection
SENSITIVE = {"order_id": "o-1", "password": SECRET, "user": {"email": "a@b.c", "name": "x"},
             "items": [{"token": "tok-1", "sku": "s1"}]}


async def g2():
    await decl("t2.work", {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": "t2.dlq"})
    await decl("t2.dlq")
    ts = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    await pub("t2.work", SENSITIVE, content_type="application/json", message_id="o-1",
              correlation_id="c-1", app_id="svc-orders", type="order.created", priority=3,
              delivery_mode=aio_pika.DeliveryMode.PERSISTENT, timestamp=ts,
              headers={"Authorization": f"Bearer {SECRET}", "api-key": SECRET[:4], "X-Api-Key": SECRET[4:], "trace": "t-1"})
    await pub("t2.work", f"plain text payload password={SECRET}".encode(), content_type="text/plain")
    await pub("t2.work", bytes(range(256)), content_type="application/octet-stream")
    await pub("t2.work", {"blob": "x" * 6000})
    await deadletter("t2.work", 4)
    await pub("t2.dlq", b"dup", message_id="dup-fixed")  # aiormq stamps a random id otherwise
    await pub("t2.dlq", b"dup", message_id="dup-fixed")

    before = await count("t2.dlq")
    p1 = await preview("t2.dlq")
    p2 = await preview("t2.dlq")
    after = await count("t2.dlq")
    check(2, "Preview is non-destructive (count unchanged)", before == after == 6 and len(p1) == 6, (before, after, len(p1)))
    check(2, "Preview is repeatable: same order, stable fingerprints",
          [m["fingerprint"] for m in p1] == [m["fingerprint"] for m in p2])
    check(2, "Previewed messages come back marked redelivered (documented)", all(m["redelivered"] for m in p2))
    fmts = [m["payload_format"] for m in p1]
    check(2, "JSON / text / base64 rendering", fmts[:3] == ["json", "text", "base64"], fmts)

    m = p1[0]
    props = m["properties"]
    check(2, "AMQP properties surfaced",
          props["message_id"] == "o-1" and props["correlation_id"] == "c-1" and props["app_id"] == "svc-orders"
          and props["type"] == "order.created" and props["priority"] == 3 and props["delivery_mode"] == 2
          and props["timestamp"].startswith("2026-09-30T12:00"), props)
    check(2, "Exchange + routing key surfaced", m["exchange"] == "" and m["routing_key"] == "t2.dlq",
          (m["exchange"], m["routing_key"]))
    xd = m["x_death"][0] if m["x_death"] else {}
    check(2, "Parsed x-death: queue, exchange, reason, count, routing-keys, time",
          xd.get("queue") == "t2.work" and xd.get("reason") == "rejected" and xd.get("count") == 1
          and xd.get("routing-keys") == ["t2.work"] and isinstance(xd.get("time"), str), xd)
    check(2, "Fingerprint is a sha-256 hex digest", re.fullmatch(r"[0-9a-f]{64}", m["fingerprint"]))

    pl, hd = m["payload"], m["headers"]
    check(2, "Masking: payload keys (top-level, nested, inside lists)",
          pl["password"] == "***" and pl["user"]["email"] == "***" and pl["items"][0]["token"] == "***"
          and pl["user"]["name"] == "x" and pl["items"][0]["sku"] == "s1", pl)
    check(2, "Masking: headers (Authorization, api-key)",
          hd["Authorization"] == "***" and hd["api-key"] == "***" and hd["trace"] == "t-1", hd)
    check(2, "Masking: 'X-Api-Key' header is masked", hd.get("X-Api-Key") == "***",
          f"X-Api-Key rendered as {hd.get('X-Api-Key')!r} — exact-key match only, the x- prefix defeats it",
          bad="LIMIT")
    check(2, "Masking: secrets inside text payloads",
          SECRET not in str(p1[1]["payload"]), p1[1]["payload"], bad="LIMIT")
    big = p1[3]
    check(2, "Oversized payload truncated with marker",
          big["payload_truncated"] and "truncated" in big["payload"], big["payload"])

    fp = m["fingerprint"]
    d = await api("GET", f"/api/queues/t2.dlq/messages/{fp}")
    check(2, "Detail (X-ray) lookup by fingerprint, still masked",
          d.status_code == 200 and d.json()["message"]["payload"]["password"] == "***", d.status_code)
    dup_fp = p1[4]["fingerprint"]
    check(2, "Byte-identical duplicates share a fingerprint (best-effort identity)",
          p1[4]["fingerprint"] == p1[5]["fingerprint"], bad="FAIL")
    check(2, "Ambiguous fingerprint → 404 on detail (fail closed)",
          (await api("GET", f"/api/queues/t2.dlq/messages/{dup_fp}")).status_code == 404)
    check(2, "Unknown fingerprint → 404",
          (await api("GET", f"/api/queues/t2.dlq/messages/{'0' * 64}")).status_code == 404)
    check(2, "Detail lookup needs the full fingerprint (prefix → 404)",
          (await api("GET", f"/api/queues/t2.dlq/messages/{fp[:8]}")).status_code == 404, bad="NOTE")

    # concurrent previews: basic_get holds messages unacked while a preview runs
    await asyncio.gather(*(preview("t2.dlq") for _ in range(4)))
    lens = [len(x) for x in await asyncio.gather(*(preview("t2.dlq") for _ in range(6)))]
    check(2, "Concurrent previews see the whole queue", min(lens) == 6, f"preview sizes {lens}", bad="NOTE")

    # quorum DLQ with a delivery limit: every preview is a delivery + requeue
    await decl("t2.quorum.dlq", {"x-queue-type": "quorum", "x-delivery-limit": 2})
    await pub("t2.quorum.dlq", {"precious": True})
    await until(lambda: _eq(count("t2.quorum.dlq"), 1), 5)
    await wait_stats("t2.quorum.dlq")  # otherwise the 409 would be "no stats yet", not the limit
    codes = []
    for _ in range(5):
        r = await preview("t2.quorum.dlq")
        codes.append(getattr(r, "status_code", 200))
        await asyncio.sleep(0.2)
    left = await count("t2.quorum.dlq")
    await decl("t2.quorum.control", {"x-queue-type": "quorum", "x-delivery-limit": 2})
    await pub("t2.quorum.control", {"precious": True})
    await asyncio.sleep(1.5)
    control = await count("t2.quorum.control")
    detail = (await preview("t2.quorum.dlq")).json().get("detail", "")
    row = (await api("GET", "/api/queues/t2.quorum.dlq")).json()["queue"]
    check(2, "Queue list flags the quorum DLQ as not browsable (delivery limit 2)",
          row.get("browsable") is False and row.get("delivery_limit") == 2,
          {k: row.get(k) for k in ("queue_type", "delivery_limit", "browsable")})
    check(2, "Browsing never consumes — quorum DLQ with x-delivery-limit=2, previewed 5x",
          left == 1 and set(codes) == {409} and "delivery limit of 2" in detail,
          f"preview codes {codes}; messages left {left} (control queue, never previewed: {control})")

    # deep browsing (#4): one snapshot pages far past the preview window, and an action
    # picked from it reaches the message
    await decl("t2.deep.dlq")
    for i in range(160):
        await pub("t2.deep.dlq", {"n": i}, message_id=f"d{i}")
    await wait_ql_count("t2.deep.dlq", 160)
    base = "/api/queues/t2.deep.dlq/messages"
    first = (await api("GET", f"{base}?snapshot=new&limit=50")).json()
    sid = first["snapshot"]["id"]
    tail = (await api("GET", f"{base}?snapshot={sid}&offset=150&limit=50")).json()["messages"]
    check(2, "A snapshot pages past the preview window (one scan, offset 150)",
          first["total"] == 160 and [m["message_id"] for m in tail] == [f"d{i}" for i in range(150, 160)],
          (first.get("total"), [m["message_id"] for m in tail][:3]))
    deep = next(m for m in tail if m["message_id"] == "d155")
    body = {"source_queue": "t2.deep.dlq", "fingerprint": deep["fingerprint"], "confirm": True}
    plain = await api("POST", "/api/messages/park", json=body)
    reached = await api("POST", "/api/messages/park", json={**body, "snapshot": sid})
    check(2, "An action reaches a message deep in its snapshot (past the 100-message window)",
          plain.status_code == 409 and reached.status_code == 200
          and await count("t2.deep.dlq.parking") == 1, (plain.status_code, reached.status_code))

    # a quorum queue returns messages to the back: QueueLens reads it whole, requeues in order
    async with httpx.AsyncClient(auth=MAUTH) as m:
        major = int((await m.get(f"{MGMT}/api/overview")).json()["rabbitmq_version"][0])
        args = {"x-queue-type": "quorum", **({"x-delivery-limit": -1} if major >= 4 else {})}
        await m.put(f"{MGMT}/api/queues/%2F/t2.quorum.order", json={"durable": True, "arguments": args})
    for i in range(8):
        await pub("t2.quorum.order", {"q": i}, message_id=f"q{i}")
    await until(lambda: _eq(count("t2.quorum.order"), 8), 10)
    await wait_stats("t2.quorum.order")
    looks = []
    for _ in range(3):
        r = await preview("t2.quorum.order", limit=3)
        looks.append([x["message_id"] for x in r] if isinstance(r, list) else getattr(r, "status_code", r))
    await until(lambda: _eq(count("t2.quorum.order"), 8), 10)
    order = [x.message_id for x in await drain("t2.quorum.order")]
    check(2, "Browsing a quorum DLQ leaves it as it was (read whole, requeued in order)",
          looks == [["q0", "q1", "q2"]] * 3 and order == [f"q{i}" for i in range(8)], (looks, order))


# ================================================================== G3 compressed payloads
async def g3():
    await decl("t3.dlq")
    await decl("t3.target")
    gz = gzip.compress(json.dumps({"kind": "gzip", "password": SECRET}).encode())
    await pub("t3.dlq", gz, content_encoding="gzip", content_type="application/json")
    await pub("t3.dlq", zlib.compress(json.dumps({"kind": "zlib"}).encode()), content_encoding="deflate")
    co = zlib.compressobj(wbits=-15)
    await pub("t3.dlq", co.compress(json.dumps({"kind": "raw"}).encode()) + co.flush(), content_encoding="deflate")
    bomb = gzip.compress(b"0" * (64 * 1024 * 1024))
    await pub("t3.dlq", bomb, content_encoding="gzip")
    await pub("t3.dlq", b"definitely not gzip", content_encoding="gzip")

    t0 = time.monotonic()
    msgs = await preview("t3.dlq")
    elapsed = time.monotonic() - t0
    g, z, raw, b, bad = msgs
    check(3, "gzip detected + decoded (JSON pretty-printed)",
          g["decoded_from"] == "gzip" and g["payload_format"] == "json" and g["payload"]["kind"] == "gzip", g["decoded_from"])
    check(3, "deflate (zlib) decoded", z["decoded_from"] == "deflate" and z["payload"]["kind"] == "zlib")
    check(3, "raw deflate decoded", raw["decoded_from"] == "deflate" and raw["payload"]["kind"] == "raw")
    check(3, "Raw base64 view of the original bytes (nothing masked)",
          z["payload_encoded"] and zlib.decompress(base64.b64decode(z["payload_encoded"])) == b'{"kind": "zlib"}')
    check(3, "Zip-bomb (64 MiB inflated) left encoded, request stays fast",
          b["decoded_from"] is None and elapsed < 5, f"decoded_from={b['decoded_from']} preview took {elapsed:.2f}s, body {b['payload_size']} B")
    check(3, "Corrupt 'gzip' body falls back without error", bad["decoded_from"] is None and bad["payload_format"] == "text")
    check(3, "Masking applies to the decoded payload", g["payload"]["password"] == "***")
    leaked = bool(g["payload_encoded"]) and SECRET.encode() in gzip.decompress(base64.b64decode(g["payload_encoded"]))
    check(3, "Masked fields stay hidden in every view", not leaked,
          "payload_encoded = base64(original gzip) — decode it and the masked password is right there")

    r = await api("POST", "/api/messages/replay", json={"source_queue": "t3.dlq", "fingerprint": g["fingerprint"],
                                                          "mode": "copy", "confirm": True,
                                                          "target": {"type": "queue", "queue": "t3.target"}})
    out = await drain("t3.target")
    check(3, "Replay publishes the original compressed bytes + content_encoding",
          r.status_code == 200 and out and out[0].body == gz and out[0].content_encoding == "gzip", r.status_code)


# ================================================================== G4 replay
async def g4():
    await decl_ex("t4.ex")
    await decl("t4.work", {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": "t4.dlq"})
    for q in ("t4.dlq", "t4.target", "t4.target2", "t4.cfg.dlq", "t4.cfg.target", "t4.deep"):
        await decl(q)
    await bind("t4.target", "t4.ex", "rk.ok")
    ts = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
    for i in range(6):
        await pub("t4.work", {"n": i}, content_type="application/json", message_id=f"m-{i}",
                  correlation_id=f"c-{i}", priority=5, type="evt", app_id="svc", timestamp=ts,
                  delivery_mode=aio_pika.DeliveryMode.PERSISTENT, headers={"tenant": "acme"})
    await deadletter("t4.work", 6)
    await pub("t4.dlq", b"twin", message_id="twin-fixed")
    await pub("t4.dlq", b"twin", message_id="twin-fixed")
    await pub("t4.cfg.dlq", {"cfg": 1})
    msgs = await preview("t4.dlq")
    fp = {m["payload"]["n"]: m["fingerprint"] for m in msgs if isinstance(m["payload"], dict)}

    async def replay(fingerprint, mode="copy", target=None, source="t4.dlq", auth=ADMIN, **extra):
        body = {"source_queue": source, "fingerprint": fingerprint, "mode": mode, "confirm": True, **extra}
        if target is not None:
            body["target"] = target
        return await api("POST", "/api/messages/replay", auth=auth, json=body)

    r = await replay(fp[0], "copy", {"type": "queue", "queue": "t4.target2"})
    out = await drain("t4.target2")
    o = out[0] if out else None
    check(4, "Replay as Copy: original kept, copy delivered",
          r.status_code == 200 and await count("t4.dlq") == 8 and len(out) == 1, r.text[:200])
    if o:
        check(4, "Replay preserves body + AMQP properties",
              json.loads(o.body) == {"n": 0} and o.message_id == "m-0" and o.correlation_id == "c-0"
              and o.priority == 5 and o.type == "evt" and o.app_id == "svc" and o.delivery_mode == 2
              and o.timestamp == ts and o.content_type == "application/json",
              (o.message_id, o.correlation_id, o.priority, o.type, o.app_id, o.delivery_mode, o.timestamp))
        h = o.headers
        check(4, "Provenance headers x-queuelens-* stamped",
              h.get("x-queuelens-replayed") is True and h.get("x-queuelens-replayed-by") == "admin"
              and h.get("x-queuelens-source-queue") == "t4.dlq"
              and h.get("x-queuelens-original-fingerprint") == fp[0]
              and h.get("x-queuelens-action") == "replay_copy" and "x-queuelens-replayed-at" in h,
              {k: v for k, v in h.items() if k.startswith("x-queuelens")})
        check(4, "Original x-death history travels with the replay", "x-death" in h and h.get("tenant") == "acme")

    r = await replay(fp[1], "move", {"type": "exchange", "exchange": "t4.ex", "routing_key": "rk.ok"},
                     auth=USERS["oper1"])
    check(4, "Replay as Move through an exchange + custom routing key (Operator)",
          r.status_code == 200 and await count("t4.dlq") == 7 and await count("t4.target") == 1, r.status_code)

    async def safe(name, resp_code, fingerprint, **kw):
        r = await replay(fingerprint, "move", **kw)
        still = any(m["fingerprint"] == fingerprint for m in await preview("t4.dlq", limit=20))
        check(4, name, r.status_code == resp_code and still and await count("t4.dlq") == 7,
              f"HTTP {r.status_code} {r.json().get('detail')} — original still in DLQ: {still}")

    await safe("Unroutable publish (move) → 400, original kept", 400, fp[2],
               target={"type": "exchange", "exchange": "t4.ex", "routing_key": "routes.nowhere"})
    await safe("Missing target queue (move) → 404, original kept", 404, fp[2],
               target={"type": "queue", "queue": "t4.does.not.exist"})
    await safe("Missing target exchange (move) → 404, original kept", 404, fp[2],
               target={"type": "exchange", "exchange": "t4.no.ex", "routing_key": "k"})
    await safe("Exchange target without routing key → 400, original kept", 400, fp[2],
               target={"type": "exchange", "exchange": "t4.ex"})
    await safe("No target and none configured → 400", 400, fp[2])

    cfg = (await preview("t4.cfg.dlq"))[0]["fingerprint"]
    r = await replay(cfg, "move", source="t4.cfg.dlq")
    check(4, "Configured replay target (QUEUELENS_REPLAY_TARGETS_JSON) used when none given",
          r.status_code == 200 and await count("t4.cfg.target") == 1, r.status_code)
    r = await api("POST", "/api/messages/replay", json={"source_queue": "t4.dlq", "fingerprint": fp[2],
                                                          "target": {"type": "queue", "queue": "t4.target2"}})
    check(4, "confirm missing → 400", r.status_code == 400, r.status_code)
    r = await replay("f" * 64, "move", {"type": "queue", "queue": "t4.target2"})
    check(4, "Unknown fingerprint → 409 (fail closed)", r.status_code == 409, r.status_code)
    twin = next(m["fingerprint"] for m in msgs if m["payload"] == "twin")
    r = await replay(twin, "move", {"type": "queue", "queue": "t4.target2"})
    check(4, "Ambiguous fingerprint (2 identical messages) → 409, both kept",
          r.status_code == 409 and await count("t4.dlq") == 7 and await count("t4.target2") == 0, r.status_code)

    await drain("t4.target2")
    r = await replay(fp[3], "copy", {"type": "queue", "queue": "t4.target2"}, annotate=False)
    o = (await drain("t4.target2"))[0]
    check(4, "annotate=false → no provenance headers",
          not any(k.startswith("x-queuelens") for k in o.headers), list(o.headers))

    await put_settings({"custom_headers": [{"key": "x-team", "value": "sre"}]})
    await replay(fp[3], "copy", {"type": "queue", "queue": "t4.target2"})
    o = (await drain("t4.target2"))[0]
    check(4, "Admin custom headers stamped on replays", o.headers.get("x-team") == "sre")

    # masking is display-only: the replayed bytes are the original ones
    m = (await preview("t2.dlq"))[0]
    await replay(m["fingerprint"], "copy", {"type": "queue", "queue": "t4.target2"}, source="t2.dlq")
    o = (await drain("t4.target2"))[0]
    check(4, "Masking is display-only (replayed body still has the real secret)",
          json.loads(o.body)["password"] == SECRET and o.headers.get("Authorization") == f"Bearer {SECRET}")

    async with httpx.AsyncClient(auth=MAUTH) as mg:  # mgmt publish: no client-side message_id
        await mg.post(f"{MGMT}/api/exchanges/%2F/amq.default/publish", json={
            "properties": {}, "routing_key": "t4.dlq", "payload": "no-id", "payload_encoding": "string"})
    noid = next(m for m in await preview("t4.dlq", limit=20) if m["payload"] == "no-id")
    await replay(noid["fingerprint"], "copy", {"type": "queue", "queue": "t4.target2"})
    o = (await drain("t4.target2"))[0]
    check(4, "Replay copies properties exactly (no message_id invented)", o.message_id is None,
          f"original message_id=None → replayed message_id={o.message_id!r} (aiormq; documented in SAFETY.md)",
          bad="LIMIT")

    for i in range(70):
        await pub("t4.deep", {"deep": i})
    await put_settings({"limits": {"max_preview_messages": 70}})
    deep = await preview("t4.deep", limit=70)
    await put_settings({"limits": {}})
    r = await replay(deep[65]["fingerprint"], "move", {"type": "queue", "queue": "t4.target2"}, source="t4.deep")
    check(4, "Single actions are bounded by the re-fetch window (msg #66 of 70, window 60 → 409)",
          r.status_code == 409 and await count("t4.deep") == 70, r.status_code)


# ================================================================== G5 park
async def g5():
    await decl("t5.work", {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": "t5.dlq"})
    await decl("t5.dlq")
    await pub("t5.work", {"poison": 1})
    await pub("t5.work", {"poison": 2})
    await deadletter("t5.work", 2)
    msgs = await preview("t5.dlq")
    r = await api("POST", "/api/messages/park", auth=USERS["oper1"],
                  json={"source_queue": "t5.dlq", "fingerprint": msgs[0]["fingerprint"], "confirm": True})
    async with httpx.AsyncClient(auth=MAUTH) as m:
        parking = (await m.get(f"{MGMT}/api/queues/%2F/t5.dlq.parking")).json()
    check(5, "Park moves the message to auto-created durable {queue}.parking",
          r.status_code == 200 and parking.get("durable") is True and await count("t5.dlq.parking") == 1
          and await count("t5.dlq") == 1, (r.status_code, parking.get("durable")))
    o = (await drain("t5.dlq.parking"))[0]
    h = o.headers
    check(5, "Parked message keeps x-death + gets park provenance",
          "x-death" in h and h.get("x-queuelens-action") == "park" and "x-queuelens-parked-at" in h
          and h.get("x-queuelens-source-queue") == "t5.dlq", [k for k in h if k.startswith("x-")])
    await pub("t5.dlq.parking", o.body, headers=h)
    await wait_ql_count("t5.dlq.parking", 1)
    q = (await api("GET", "/api/queues/t5.dlq.parking")).json()["queue"]
    lst = {x["name"]: x for x in (await api("GET", "/api/queues", params={"dlq_only": "true"})).json()["queues"]}
    check(5, "Parking queues are classified as parking", lst.get("t5.dlq.parking", {}).get("kind") == "parking"
          and lst["t5.dlq.parking"]["status"] == "parking", lst.get("t5.dlq.parking"))
    r = await api("POST", "/api/messages/park", json={"source_queue": "t5.dlq", "fingerprint": msgs[1]["fingerprint"]})
    check(5, "Park without confirm → 400", r.status_code == 400)
    r = await api("POST", "/api/messages/park", json={"source_queue": "t5.dlq", "fingerprint": "e" * 64, "confirm": True})
    check(5, "Park unknown fingerprint → 409", r.status_code == 409)
    _ = q


# ================================================================== G6 delete
async def g6():
    await decl("t6.dlq")
    for i in range(3):
        await pub("t6.dlq", {"d": i})
    msgs = await preview("t6.dlq")
    body = {"source_queue": "t6.dlq", "fingerprint": msgs[0]["fingerprint"], "confirm": True}
    r1 = await api("POST", "/api/messages/delete", auth=OPSENV, json=body)
    r2 = await api("POST", "/api/messages/delete", json={**body, "confirm": False})
    check(6, "Delete is Admin-only (Operator 403) and needs confirm (400)",
          r1.status_code == 403 and r2.status_code == 400 and await count("t6.dlq") == 3, (r1.status_code, r2.status_code))
    r = await api("POST", "/api/messages/delete", auth=USERS["admin2"], json=body)
    check(6, "Admin delete removes exactly that message",
          r.status_code == 200 and await count("t6.dlq") == 2
          and msgs[0]["fingerprint"] not in [m["fingerprint"] for m in await preview("t6.dlq")])
    ev = await audit(action="delete", source_queue="t6.dlq")
    check(6, "Delete attempt + result audited", sorted(e["result"] for e in ev) == ["started", "success"]
          and ev[0]["username"] == "admin2", [(e["result"], e["username"]) for e in ev])


# ================================================================== G7 bulk
EXPIRY_TOKEN = {}


async def g7():
    await decl("t7.dlq")
    await decl("t7.target")
    for i in range(5):
        await pub("t7.dlq", {"tenant": "acme", "i": i})
    for i in range(3):
        await pub("t7.dlq", {"tenant": "globex", "i": i})
    await pub("t7.dlq", b"dup-bulk", message_id="dupb-fixed")
    await pub("t7.dlq", b"dup-bulk", message_id="dupb-fixed")

    r = await api("POST", "/api/messages/bulk/dry-run",
                  json={"source_queue": "t7.dlq", "action": "park", "payload_contains": "globex"})
    EXPIRY_TOKEN["id"], EXPIRY_TOKEN["t"] = r.json()["batch_id"], time.monotonic()

    dr = (await api("POST", "/api/messages/bulk/dry-run", json={
        "source_queue": "t7.dlq", "action": "replay", "mode": "move",
        "target": {"type": "queue", "queue": "t7.target"}, "payload_contains": '"tenant": "acme"'})).json()
    check(7, "Payload-filter dry-run counts exactly the matching messages",
          dr["message_count"] == 5 and dr["unique_fingerprints"] == 5 and dr["duplicate_fingerprints"] == 0
          and dr["scan_limit"] == 20 and len(dr["sample_fingerprints"]) == 5,
          {k: dr[k] for k in ("message_count", "unique_fingerprints", "scan_limit", "expires_at")})
    await pub("t7.dlq", {"tenant": "acme", "i": "late"})
    r = await api("POST", "/api/messages/bulk/execute", json={"batch_id": dr["batch_id"]})
    check(7, "Execute without confirm → 400", r.status_code == 400)
    ex = await api("POST", "/api/messages/bulk/execute", json={"batch_id": dr["batch_id"], "confirm": True})
    s = ex.json()["summary"]
    remaining = [m["payload"] for m in await preview("t7.dlq", limit=20)]
    check(7, "Execute acts on the dry-run set only (late arrival untouched)",
          s["succeeded"] == 5 and await count("t7.target") == 5 and {"tenant": "acme", "i": "late"} in remaining,
          s)
    check(7, "Per-message results returned", len(ex.json()["results"]) == 5
          and all(x["status"] == "success" for x in ex.json()["results"]))
    again = await api("POST", "/api/messages/bulk/execute", json={"batch_id": dr["batch_id"], "confirm": True})
    check(7, "Confirmation token is one-shot (re-use → 404)", again.status_code == 404, again.status_code)

    out = await drain("t7.target")
    h = out[0].headers if out else {}
    check(7, "Bulk replay stamps the same provenance as single replay (x-queuelens-source-queue)",
          "x-queuelens-source-queue" in h and "x-queuelens-action" in h,
          f"bulk headers: {sorted(k for k in h if k.startswith('x-queuelens'))}")
    check(7, "Bulk replay stamps admin custom headers (x-team)", h.get("x-team") == "sre",
          f"x-team={h.get('x-team')!r} — single replay and composer stamp it, bulk does not")

    globex = [m for m in await preview("t7.dlq", limit=20) if isinstance(m["payload"], dict) and m["payload"].get("tenant") == "globex"]
    dr = (await api("POST", "/api/messages/bulk/dry-run", json={
        "source_queue": "t7.dlq", "action": "park", "fingerprints": [globex[0]["fingerprint"], "9" * 64]})).json()
    check(7, "Selection-based dry-run (+ selected_not_seen)", dr["message_count"] == 1 and dr["selected_not_seen"] == 1, dr)
    ex = (await api("POST", "/api/messages/bulk/execute", json={"batch_id": dr["batch_id"], "confirm": True})).json()
    parked = await drain("t7.dlq.parking")
    ph = parked[0].headers if parked else {}
    check(7, "Bulk park parks the selection", ex["summary"]["succeeded"] == 1 and len(parked) == 1, ex["summary"])
    check(7, "Bulk-parked message is labelled as parked (not as replayed)",
          ph.get("x-queuelens-action") == "park" and not ph.get("x-queuelens-replayed"),
          f"bulk park headers: { {k: v for k, v in ph.items() if k.startswith('x-queuelens')} }")

    dr = (await api("POST", "/api/messages/bulk/dry-run", auth=USERS["admin2"], json={
        "source_queue": "t7.dlq", "action": "delete", "payload_contains": "dup-bulk"})).json()
    check(7, "Duplicates detected in dry-run", dr["message_count"] == 2 and dr["duplicate_fingerprints"] == 1, dr)
    r = await api("POST", "/api/messages/bulk/execute", auth=OPSENV, json={"batch_id": dr["batch_id"], "confirm": True})
    check(7, "Operator cannot execute an Admin's bulk-delete token (403)", r.status_code == 403)
    r = await api("POST", "/api/messages/bulk/dry-run", auth=OPSENV,
                  json={"source_queue": "t7.dlq", "action": "delete"})
    check(7, "Operator cannot dry-run bulk delete (403)", r.status_code == 403)
    ex = (await api("POST", "/api/messages/bulk/execute", auth=USERS["admin2"],
                    json={"batch_id": dr["batch_id"], "confirm": True})).json()
    left = [m["payload"] for m in await preview("t7.dlq", limit=20)].count("dup-bulk")
    check(7, "Ambiguous duplicates skipped, never guessed (both kept)",
          ex["summary"]["skipped_duplicates"] == 1 and left == 2, ex["summary"])

    n = await count("t7.dlq")
    dr = (await api("POST", "/api/messages/bulk/dry-run", json={
        "source_queue": "t7.dlq", "action": "replay", "mode": "move", "payload_contains": "globex",
        "target": {"type": "exchange", "exchange": "t4.ex", "routing_key": "routes.nowhere"}})).json()
    ex = (await api("POST", "/api/messages/bulk/execute", json={"batch_id": dr["batch_id"], "confirm": True})).json()
    check(7, "Unroutable bulk move: each message fails independently and stays",
          ex["summary"]["failed"] == dr["message_count"] > 0 and await count("t7.dlq") == n, ex["summary"])
    dr = (await api("POST", "/api/messages/bulk/dry-run", json={
        "source_queue": "t7.dlq", "action": "replay", "mode": "move",
        "target": {"type": "queue", "queue": "t7.no.such.queue"}})).json()
    r = await api("POST", "/api/messages/bulk/execute", json={"batch_id": dr["batch_id"], "confirm": True})
    check(7, "Missing bulk target aborts before anything is consumed (404)",
          r.status_code == 404 and await count("t7.dlq") == n, r.status_code)

    await decl("t7.big")
    for i in range(30):
        await pub("t7.big", {"big": i})
    dr = (await api("POST", "/api/messages/bulk/dry-run", json={"source_queue": "t7.big", "action": "park"})).json()
    check(7, "Hard bulk limit: 30 queued, env cap 20 → dry-run sees 20", dr["message_count"] == 20, dr["message_count"])
    await put_settings({"limits": {"max_bulk_size": 50}})
    dr = (await api("POST", "/api/messages/bulk/dry-run", json={"source_queue": "t7.big", "action": "park"})).json()
    ex = (await api("POST", "/api/messages/bulk/execute", json={"batch_id": dr["batch_id"], "confirm": True})).json()
    await put_settings({"limits": {}})
    check(7, "UI limit override is reported truthfully by the dry run",
          dr["message_count"] == 30 and dr["scan_limit"] == 50,
          f"UI limits.max_bulk_size=50 → approved {dr['message_count']}, scan_limit {dr['scan_limit']}")
    check(7, "Execute acts on everything the dry-run approved",
          ex["summary"]["succeeded"] == 30 and ex["summary"]["not_found"] == 0, ex["summary"])
    await drain("t7.big.parking")

    ev = [e for e in await audit() if (e.get("metadata") or {}).get("batch_id")]
    results = {e["result"] for e in ev}
    check(7, "Bulk audit: one event per message + bulk_<action> envelope",
          any(e["action"] == "bulk_replay" for e in ev) and any(e["action"] == "replay" for e in ev),
          sorted({(e['action'], e['result']) for e in ev}))
    check(7, "Bulk records an attempt ('started') before touching the broker", "started" in results,
          f"bulk results seen: {sorted(results)} — events are written only after the broker work")


# ================================================================== G8 composer
async def g8():
    await decl("t8.q")
    await decl("t8.work", {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": "t8.dlq"})
    await decl("t8.dlq")

    async def publish(**body):
        return await api("POST", "/api/messages/publish", json={"confirm": True, **body})

    r = await publish(routing_key="t8.q", payload=json.dumps({"hello": 1}))
    o = (await drain("t8.q"))[0]
    check(8, "Publish JSON test message to a queue", r.status_code == 200 and o.content_type == "application/json"
          and o.headers.get("x-queuelens-test") is True and o.headers.get("x-queuelens-published-by") == "admin"
          and o.headers.get("x-team") == "sre", dict(o.headers))
    r = await publish(routing_key="t8.q", payload="just text", mark_test=False)
    o = (await drain("t8.q"))[0]
    check(8, "Plain-text payload + mark_test=false", o.content_type == "text/plain" and "x-queuelens-test" not in o.headers)
    await drain("t4.target")
    r = await publish(exchange="t4.ex", routing_key="rk.ok", payload="{}")
    check(8, "Publish through an exchange + routing key", r.status_code == 200 and len(await drain("t4.target")) == 1)
    codes = (
        (await publish(exchange="t4.ex", routing_key="routes.nowhere", payload="{}")).status_code,
        (await publish(routing_key="t8.missing", payload="{}")).status_code,
        (await publish(exchange="t8.nope", routing_key="k", payload="{}")).status_code,
        (await api("POST", "/api/messages/publish", json={"routing_key": "t8.q", "payload": "x"})).status_code,
    )
    check(8, "Unroutable 400 / missing queue 404 / missing exchange 404 / no confirm 400",
          codes == (400, 404, 404, 400), codes)
    await publish(routing_key="t8.q", payload="{}", headers={"x-custom": "1"},
                  properties={"message_id": "mine", "priority": 7})
    o = (await drain("t8.q"))[0]
    check(8, "Composer: custom per-message headers/properties",
          o.headers.get("x-custom") == "1" and o.message_id == "mine",
          f"x-custom={o.headers.get('x-custom')!r} message_id={o.message_id!r} — the API has no headers/properties fields (silently ignored)")
    await publish(routing_key="t8.work", payload=json.dumps({"will": "fail"}))
    await deadletter("t8.work", 1)
    d = await preview("t8.dlq")
    check(8, "Reproduce a failure: composed message dead-letters with x-death",
          d and d[0]["x_death"] and d[0]["x_death"][0]["queue"] == "t8.work")
    ev = await audit(action="publish")
    check(8, "Every publish attempt audited (success + failed)",
          {"success", "failed"} <= {e["result"] for e in ev}, sorted({e["result"] for e in ev}))


# ================================================================== G9 topology
TOPO = {}


async def g9():
    await asyncio.sleep(max(0, 31 - (time.monotonic() - TOPO["g1"])))  # let g1's cached snapshot expire
    t = (await api("GET", "/api/topology")).json()
    TOPO["t0"] = time.monotonic()
    ex = {e["name"] for e in t["exchanges"]}
    check(9, "Exchanges listed (internal + default hidden)", "t4.ex" in ex and "" not in ex
          and "amq.rabbitmq.trace" not in ex, sorted(ex)[:8])
    check(9, "Bindings listed", any(b["source"] == "t4.ex" and b["destination"] == "t4.target"
                                    and b["routing_key"] == "rk.ok" for b in t["bindings"]))
    q = next((x for x in t["queues"] if x["name"] == "t1.payments.work"), {})
    check(9, "Dead-letter relationships (dlx + dlx routing key) on queues",
          q.get("dlx") == "" and q.get("dlx_routing_key") == "t1.payments.failed", q)
    await decl("t9.new")
    t2 = (await api("GET", "/api/topology")).json()
    check(9, "Topology snapshot is cached (new queue not visible inside 30s)",
          "t9.new" not in {x["name"] for x in t2["queues"]})
    exs = {e["name"] for e in (await api("GET", "/api/exchanges")).json()["exchanges"]}
    check(9, "/api/exchanges hides internal exchanges", "amq.rabbitmq.trace" not in exs and "t4.ex" in exs)


# ================================================================== G13 audit
async def g13():
    ev = await audit(source_queue="t4.dlq", action="replay")
    copy_started = [e for e in ev if e["result"] == "started" and (e["metadata"] or {}).get("mode") == "copy"]
    ok = [e for e in ev if e["result"] == "success"]
    check(13, "Attempt ('started') recorded before execution, outcome after",
          copy_started and ok, f"{len(copy_started)} started / {len(ok)} success")
    e = ok[-1] if ok else {}
    md = e.get("metadata") or {}
    check(13, "Outcome carries user, timestamp, duration, target, mode, headers, x-death",
          e.get("username") and e.get("timestamp") and "duration_ms" in md and e.get("target_type")
          and "mode" in md and "headers_added" in md and "x_death" in md, sorted(md))
    failed = await audit(result="failed", source_queue="t4.dlq")
    check(13, "Failures audited with the error", failed and all(f["error_message"] for f in failed), len(failed))
    f1 = await audit(username="oper1")
    f2 = await audit(action="park")
    lim = (await api("GET", "/api/audit", params={"limit": 2})).json()["events"]
    check(13, "Filters: username / action / limit", f1 and all(x["username"] == "oper1" for x in f1)
          and f2 and all(x["action"] == "park" for x in f2) and len(lim) == 2)
    allev = await audit()
    check(13, "request_ip / user_agent captured", any(x["request_ip"] or x["user_agent"] for x in allev),
          "audit_events has the columns but no code path fills them — every row is null")

    rj = await api("GET", "/api/audit/export", params={"format": "json"})
    rc = await api("GET", "/api/audit/export", params={"format": "csv"})
    rows = json.loads(rj.text)
    total = db_rows("select count(*) from audit_events")[0][0]
    csv_lines = rc.text.strip().splitlines()
    check(13, "Full-history JSON + CSV export (attachment)",
          len(rows) == total and len(csv_lines) == total + 1 and "attachment" in rc.headers["content-disposition"],
          f"db={total} json={len(rows)} csv={len(csv_lines) - 1}")
    await api("POST", "/api/messages/park", json={"source_queue": "=HYPERLINK(\"http://x\")", "fingerprint": "a" * 64, "confirm": True})
    rc = await api("GET", "/api/audit/export", params={"format": "csv"})
    check(13, "CSV export neutralises spreadsheet formulas", '"=HYPERLINK' not in rc.text,
          "queue-name cell exported as \"=HYPERLINK(...)\" — opens as a live formula in Excel/Sheets", bad="NOTE")

    await put_settings({"ui": {"syslog": True}})
    await api("POST", "/api/messages/publish", json={"routing_key": "t8.q", "payload": "syslog", "confirm": True})
    await asyncio.sleep(0.5)
    check(13, "Audit stream to stdout (syslog toggle)", "queuelens.audit {" in open(LOG).read())
    await put_settings({"ui": {}})
    await drain("t8.q")

    # audit store failure: rename the table under the running app
    await decl("t13.dlq")
    await decl("t13.target")
    for i in range(3):
        await pub("t13.dlq", {"a": i})
    msgs = await preview("t13.dlq")
    dr = (await api("POST", "/api/messages/bulk/dry-run", json={
        "source_queue": "t13.dlq", "action": "replay", "mode": "move",
        "target": {"type": "queue", "queue": "t13.target"}, "fingerprints": [m["fingerprint"] for m in msgs[1:]]})).json()
    db_exec("ALTER TABLE audit_events RENAME TO audit_events_off")
    # These fail inside the app (500 from an unhandled error), after which uvicorn closes
    # the keep-alive connection; "Connection: close" keeps httpx from reusing it for the
    # next request (on Linux that reuse races the FIN and surfaces as ReadError).
    once = {"Connection": "close"}
    try:
        r1 = await api("POST", "/api/messages/replay", headers=once, json={"source_queue": "t13.dlq", "fingerprint": msgs[0]["fingerprint"],
                                                                           "mode": "move", "confirm": True,
                                                                           "target": {"type": "queue", "queue": "t13.target"}})
        n_single = (await count("t13.dlq"), await count("t13.target"))
        r2 = await api("POST", "/api/messages/bulk/execute", headers=once, json={"batch_id": dr["batch_id"], "confirm": True})
        n_bulk = (await count("t13.dlq"), await count("t13.target"))
        r3 = await api("POST", "/api/messages/publish", headers=once, json={"routing_key": "t13.target", "payload": "x", "confirm": True})
        n_pub = await count("t13.target")
    finally:
        db_exec("ALTER TABLE audit_events_off RENAME TO audit_events")
    check(13, "Single action refused when the audit attempt can't be written",
          r1.status_code >= 500 and n_single == (3, 0), f"HTTP {r1.status_code}, (dlq, target)={n_single}")
    check(13, "Bulk execute refused when the audit can't be written",
          n_bulk == (3, 0), f"HTTP {r2.status_code}, (dlq, target)={n_bulk} — must stay (3, 0): nothing moves unaudited")
    check(13, "Composer publish refused when the audit can't be written",
          n_pub == n_bulk[1], f"HTTP {r3.status_code}, target {n_bulk[1]}→{n_pub} — must not change: nothing publishes unaudited")


# ================================================================== G20 configuration
async def g20():
    cfg = (await api("GET", "/api/config")).json()
    check(20, "Env-var config surfaced read-only",
          cfg["max_preview_messages"] == 10 and cfg["max_bulk_size"] == 20 and cfg["max_message_size_bytes"] == 4096
          and cfg["replay_targets"] == ["t4.cfg.dlq"] and "password" in cfg["masked_fields"],
          {k: cfg[k] for k in ("max_preview_messages", "max_bulk_size", "refetch_window_size")})
    raw = json.dumps(cfg)
    check(20, "/api/config leaks no secrets", not any(s in raw for s in (ADMIN[1], MAUTH[1] + "@", KEY, PW["broken"])))
    broker = (await api("GET", "/api/broker")).json()
    check(20, "/api/broker shows host:port without credentials", broker["host"] == f"{BROKER}:{AMQP_PORT}"
          and f"{MAUTH[0]}:" not in json.dumps(broker), broker)
    await put_settings({"limits": {"max_preview_messages": 3}})
    n3 = len(await preview("t4.deep"))
    await put_settings({"limits": {}})
    n10 = len(await preview("t4.deep"))
    check(20, "Precedence: UI limit overrides env default, env default otherwise", (n3, n10) == (3, 10), (n3, n10))
    over = await preview("t4.deep", limit=70, auth=USERS["viewer1"])
    check(20, "Preview cap enforced (env QUEUELENS_MAX_PREVIEW_MESSAGES=10)", len(over) <= 10,
          f"Viewer GET ?limit=70 → {len(over)} messages (hard ceiling is 1000; docs say 1–100)")
    r = await put_settings({"bogus": 1})
    check(20, "Unknown settings key rejected (400)", r.status_code == 400)


# ================================================================== G21 secrets
async def g21():
    chan = {"email": {"smtp_host": SMTP_HOST, "smtp_port": SMTP_PORT, "from": "ql@test.local",
                      "to": "alerts@test.local", "password": SMTP_SECRET},
            "slack": {"url": f"{HOOK}/slack/T000/B000/{SLACK_SECRET}"},
            "webhook": {"url": f"{HOOK}/webhook"},
            "pagerduty": {"url": f"{HOOK}/pd", "routing_key": PD_SECRET}}
    await put_settings({"channels": chan})
    got = (await api("GET", "/api/settings", auth=USERS["viewer1"])).json()["channels"]
    check(21, "SMTP password is write-only (API returns __secret__)", got["email"]["password"] == "__secret__")
    check(21, "Webhook/Slack URLs + PagerDuty routing key hidden from read-only users",
          not any(v in json.dumps(got) for v in (SLACK_SECRET, PD_SECRET)),
          f"Viewer sees slack.url={got['slack']['url'][-28:]!r} pagerduty.routing_key={got['pagerduty'].get('routing_key')!r}")
    chan["email"]["password"] = "__secret__"
    chan["pagerduty"] = {"url": f"{HOOK}/pd"}  # never let a real routing key reach PagerDuty from tests
    await put_settings({"channels": chan})
    rows = dict(db_rows("select key, value from app_settings"))
    enc = json.loads(rows["channels"])
    plain = json.loads(Fernet(KEY.encode()).decrypt(enc["__encrypted__"].encode()))
    check(21, "Re-saving with the sentinel keeps the stored password", plain["email"]["password"] == SMTP_SECRET)
    check(21, "Secret-bearing settings encrypted at rest (Fernet)",
          "__encrypted__" in rows["channels"] and SMTP_SECRET.encode() not in stored_bytes())


# ================================================================== G15–G18 alerts, notifications, delivery, quiet hours
async def g15_18():
    for q in ("t15.payments.a", "t15.payments.b", "t18.q"):
        await decl(q)
    bad = [
        {"name": "x", "metric": "bogus"}, {"name": "x", "duration_seconds": 90000},
        {"name": "x", "channels": ["sms"]}, {"name": ""},
    ]
    codes = [(await api("POST", "/api/alerts", json=b)).status_code for b in bad]
    check(15, "Rule validation (metric, duration, channel, name) → 422", set(codes) == {422}, codes)
    HOOKS.clear()
    r1 = (await api("POST", "/api/alerts", auth=USERS["oper1"], json={
        "name": "t15 backlog", "pattern": "t15.payments.*", "metric": "messages", "operator": ">",
        "threshold": 2, "severity": "Alert", "channels": ["webhook", "slack", "email"]})).json()
    for i in range(3):
        await pub("t15.payments.a", {"i": i})

    async def fired(title):
        return [n for n in await notifications() if n["title"] == title]

    t0 = time.monotonic()
    got = await until(lambda: fired("Rule fired: t15 backlog"), 25)
    check(15, "Pattern rule (t15.payments.*) fires on threshold", got, f"after {time.monotonic() - t0:.1f}s: {got[0]['message'] if got else None}")
    if got:
        d = got[0]["delivery"]
        check(17, "Delivered to webhook + Slack + email, outcomes tracked",
              all(d.get(c, {}).get("ok") for c in ("webhook", "slack", "email")), d)
        wh = [h for h in HOOKS if h["path"] == "/webhook"]
        sl = [h for h in HOOKS if h["path"].startswith("/slack")]
        check(17, "Generic webhook payload {title, message, source}",
              wh and wh[0]["json"].get("source") == "queuelens" and "t15.payments.a=3" in wh[0]["json"]["message"])
        check(17, "Slack payload {text}", sl and "Rule fired" in sl[0]["json"].get("text", ""))
        mails = [m for m in await mailpit_messages() if "Rule fired: t15 backlog" in m["Subject"]]
        check(17, "Email alert via SMTP", mails, mails[0]["Subject"] if mails else None)
    await asyncio.sleep(4)
    check(15, "Fires once, not every evaluation pass", len(await fired("Rule fired: t15 backlog")) == 1)
    async with CONN.channel() as ch:
        await (await ch.declare_queue("t15.payments.a", passive=True)).purge()
    rec_n = await until(lambda: fired("Recovered: t15 backlog"), 25)
    check(15, "Recovery notification when the condition clears",
          rec_n and rec_n[0]["level"] == "Success" and rec_n[0]["delivery"].get("webhook", {}).get("ok"), rec_n[:1])

    r2 = (await api("POST", "/api/alerts", json={"name": "t15 hold", "pattern": "t15.payments.b",
                                                 "metric": "messages", "operator": ">=", "threshold": 1,
                                                 "duration_seconds": 8, "severity": "Info"})).json()
    await pub("t15.payments.b", {"x": 1})
    await wait_ql_count("t15.payments.b", 1)
    await asyncio.sleep(3)
    early = await fired("Rule fired: t15 hold")
    late = await until(lambda: fired("Rule fired: t15 hold"), 20)
    check(15, "Duration threshold: holds before firing", not early and late, f"early={bool(early)} late={bool(late)}")
    msgs = await drain("t15.payments.b")
    check(15, "Evaluation is count-only (message never delivered by the engine)",
          msgs and not msgs[0].redelivered, [m.redelivered for m in msgs])

    from app.application.alert_engine import AlertEngine, condition_holds
    check(15, "Operators > >= = <", condition_holds(3, ">", 2) and condition_holds(2, ">=", 2)
          and condition_holds(2, "=", 2) and condition_holds(1, "<", 2) and not condition_holds(2, ">", 2))
    p = await api("PATCH", f"/api/alerts/{r2['id']}", json={"enabled": False})
    u = await api("PUT", f"/api/alerts/{r2['id']}", json={"name": "t15 hold", "pattern": "t15.payments.b", "threshold": 9})
    d1 = await api("DELETE", f"/api/alerts/{r2['id']}")
    d2 = await api("DELETE", f"/api/alerts/{r2['id']}")
    check(15, "Rule enable/disable, update, delete, 404 after delete",
          p.json()["enabled"] is False and u.json()["threshold"] == 9 and d1.status_code == 200 and d2.status_code == 404)
    await api("DELETE", f"/api/alerts/{r1['id']}")

    # G16 notifications
    titles = [n["title"] for n in await notifications()]
    check(16, "In-app feed: alert + recovery notifications, newest first",
          "Rule fired: t15 backlog" in titles and titles.index("Recovered: t15 backlog") < titles.index("Rule fired: t15 backlog"))

    # G17 channel tests + retries
    HOOKS.clear()
    outs = {c: (await api("POST", "/api/alerts/test-channel", json={"channel": c})).json()
            for c in ("email", "slack", "webhook", "pagerduty")}
    check(17, "Test-channel for email, Slack, webhook, PagerDuty (URL mode)",
          all(o.get("ok") for o in outs.values()) and {"/pd", "/webhook"} <= {h["path"] for h in HOOKS}, outs)
    mails = [m for m in await mailpit_messages() if m["Subject"] == "[QueueLens] Test notification"]
    check(17, "Test email arrived", mails)
    chans = (await api("GET", "/api/settings")).json()["channels"]
    await put_settings({"channels": {**chans, "webhook": {"url": f"{HOOK}/fail"}}})
    t0 = time.monotonic()
    o = (await api("POST", "/api/alerts/test-channel", json={"channel": "webhook"})).json()
    check(17, "Failed delivery retried 3x with backoff, outcome recorded",
          o == {"ok": False, "attempts": 3, "errors": o.get("errors")} and len(o["errors"]) == 3
          and time.monotonic() - t0 >= 2.5, f"{o.get('attempts')} attempts in {time.monotonic() - t0:.1f}s")
    await put_settings({"channels": {**chans, "webhook": {}}})
    o = (await api("POST", "/api/alerts/test-channel", json={"channel": "webhook"})).json()
    check(17, "Unconfigured channel reported, not silently dropped", o.get("ok") is False and "not configured" in o["errors"][0], o)

    # SMTP STARTTLS against a server with an untrusted, wrong-name certificate
    await put_settings({"channels": {**chans, "email": {"smtp_host": SMTP_HOST, "smtp_port": SMTP_TLS_PORT, "use_tls": True,
                                                          "from": "ql@test.local", "to": "tls@test.local"}}})
    o = (await api("POST", "/api/alerts/test-channel", json={"channel": "email"})).json()
    tls_mail = await _mail_to("tls@test.local", MAILPIT_TLS)
    check(17, "STARTTLS refuses an untrusted / wrong-name certificate",
          not o.get("ok") and "CERTIFICATE_VERIFY_FAILED" in json.dumps(o) and not tls_mail,
          (o.get("ok"), (o.get("errors") or [""])[-1][:90]))
    import smtplib
    import ssl
    verified = True
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_TLS_PORT, timeout=5) as s:
            s.starttls(context=ssl.create_default_context())
    except ssl.SSLError:
        verified = False
    check(17, "SMTP TLS verifies the server certificate", not (o.get("ok") and not verified),
          "self-signed CN=untrusted.invalid was accepted — a verifying client rejects it; smtplib.starttls() "
          "without a context does no verification (MITM can read alert mail + SMTP creds)")
    await put_settings({"channels": chans})

    # PagerDuty Events API v2 — exercised in-process so nothing reaches pagerduty.com
    import app.application.alert_engine as ae
    sent = []

    async def fake(url, payload):
        sent.append((url, payload))
        return {"ok": True, "attempts": 1, "errors": []}

    class Store:
        async def get(self, key, default=None):
            return {"channels": {"pagerduty": {"routing_key": "R-123"}}, "ui": {}}.get(key, default)

    real, ae.post_webhook = ae.post_webhook, fake
    try:
        eng = AlertEngine(rules=None, notifications=None, settings_store=Store(), queue_service_for=None)
        await eng.dispatch(["pagerduty"], "Rule fired: x", "m", severity="Alert", dedup_key="rule-1")
        await eng.dispatch(["pagerduty"], "Recovered: x", "m", severity="Alert", dedup_key="rule-1",
                           resolve=True)
    finally:
        ae.post_webhook = real
    (u1, p1), (_, p2) = sent
    check(17, "PagerDuty Events v2 trigger (routing_key, severity, source)",
          u1 == "https://events.pagerduty.com/v2/enqueue" and p1["routing_key"] == "R-123"
          and p1["event_action"] == "trigger" and p1["payload"]["severity"] == "critical"
          and p1["payload"]["source"] == "queuelens", p1)
    check(17, "PagerDuty recovery resolves the incident",
          p2["event_action"] == "resolve" and p1.get("dedup_key") == p2.get("dedup_key") == "rule-1",
          f"recovery sends event_action={p2['event_action']!r}, no dedup_key → opens a 2nd incident instead of resolving")

    # G18 quiet hours
    now = datetime.now(UTC)
    window = {"quiet_hours": True, "quiet_from": (now - timedelta(minutes=5)).strftime("%H:%M"),
              "quiet_until": (now + timedelta(minutes=30)).strftime("%H:%M")}
    await put_settings({"ui": window})
    HOOKS.clear()
    rw = (await api("POST", "/api/alerts", json={"name": "t18 warn", "pattern": "t18.q", "metric": "messages",
                                                 "threshold": 0, "severity": "Warning", "channels": ["slack"]})).json()
    ra = (await api("POST", "/api/alerts", json={"name": "t18 page", "pattern": "t18.q", "metric": "messages",
                                                 "threshold": 0, "severity": "Alert", "channels": ["slack"]})).json()
    await pub("t18.q", {"q": 1})
    w = await until(lambda: fired("Rule fired: t18 warn"), 25)
    a = await until(lambda: fired("Rule fired: t18 page"), 10)
    check(18, "Quiet hours mute Warning deliveries",
          w and w[0]["delivery"]["slack"].get("skipped") == "quiet_hours", w[0]["delivery"] if w else None)
    check(18, "Alert severity still delivered in quiet hours", a and a[0]["delivery"]["slack"].get("ok"),
          a[0]["delivery"] if a else None)
    check(18, "Midnight-crossing window logic",
          AlertEngine._in_quiet_hours({"quiet_hours": True, "quiet_from": "22:00", "quiet_until": "07:00"}, "23:30")
          and AlertEngine._in_quiet_hours({"quiet_hours": True, "quiet_from": "22:00", "quiet_until": "07:00"}, "06:59")
          and not AlertEngine._in_quiet_hours({"quiet_hours": True, "quiet_from": "22:00", "quiet_until": "07:00"}, "12:00"))
    # same window, but "now" in UTC+14 — 14h away from the UTC clock the window was built on
    from zoneinfo import ZoneInfo
    local = datetime.now(ZoneInfo("Pacific/Kiritimati"))
    far = {"quiet_hours": True, "quiet_from": (local - timedelta(minutes=5)).strftime("%H:%M"),
           "quiet_until": (local + timedelta(minutes=30)).strftime("%H:%M")}
    bad_tz = await put_settings({"ui": {**far, "quiet_tz": "Mars/Base"}})
    await put_settings({"ui": {**far, "quiet_tz": "Pacific/Kiritimati"}})
    in_tz = await app_dispatch_warning()
    await put_settings({"ui": far})  # same clock times read as UTC → outside the window
    in_utc = await app_dispatch_warning()
    check(18, "Quiet hours follow the configured time zone (unknown zone → 400)",
          bad_tz.status_code == 400 and in_tz.get("skipped") == "quiet_hours" and "skipped" not in in_utc,
          (bad_tz.status_code, in_tz, in_utc))
    await put_settings({"ui": {}})
    for r in (rw, ra):
        await api("DELETE", f"/api/alerts/{r['id']}")
    await drain("t18.q")


# ================================================================== G19 Prometheus
async def g19():
    m1 = (await api("GET", "/metrics")).text
    await preview("t4.deep")
    m2 = (await api("GET", "/metrics")).text

    def val(text, name):
        hit = re.search(rf"^{re.escape(name)} ([0-9.e+]+)$", text, re.M)
        return float(hit.group(1)) if hit else None

    check(19, "queuelens_rabbitmq_ready = 1", val(m2, "queuelens_rabbitmq_ready") == 1.0)
    dlq = 'queuelens_dlq_messages{environment="%s",queue="t1.orders.dlq",vhost="/"}'
    check(19, "queuelens_dlq_messages per DLQ, in every environment (staging shares this vhost)",
          val(m2, dlq % "development") == 5.0 and val(m2, dlq % "staging") == 5.0,
          (val(m2, dlq % "development"), val(m2, dlq % "staging")))
    up = 'queuelens_management_up{environment="%s",vhost="%s"}'
    check(19, "An environment whose queues can't be read is down, not empty",
          val(m2, up % ("development", "/")) == 1.0 and val(m2, up % ("broken", "/")) == 0.0
          and 'environment="broken",queue=' not in m2,
          (val(m2, up % ("development", "/")), val(m2, up % ("broken", "/"))))
    check(19, "queuelens_preview_requests_total increments",
          val(m2, "queuelens_preview_requests_total") == val(m1, "queuelens_preview_requests_total") + 1)
    check(19, "queuelens_actions_total by action/result (incl. bulk envelopes)",
          val(m2, 'queuelens_actions_total{action="replay",result="success"}')
          and val(m2, 'queuelens_actions_total{action="bulk_replay",result="success"}'))
    check(19, "queuelens_operation_duration_seconds histogram",
          "queuelens_operation_duration_seconds_bucket{action=\"replay\"" in m2)
    s = (await api("GET", "/api/metrics/summary")).json()
    check(19, "JSON metrics summary consistent", s["rabbitmq_ready"] and s["dlq_backlog"] == sum(d["messages"] for d in s["dlq"]))
    y = (await api("GET", "/api/metrics/alert-rules")).text
    rules = (await api("GET", "/api/alert-rules")).json()["rules"]
    # every queuelens_* metric a rule uses must be one this server exports (a HELP line at least)
    exprs_ok = all(re.findall(r"queuelens_\w+", r["expr"])
                   and all(f"# HELP {name} " in m2 for name in re.findall(r"queuelens_\w+", r["expr"]))
                   for r in rules)
    check(19, "Bundled Prometheus alert rules served + reference real metrics",
          len(rules) == open(f"{REPO}/deploy/prometheus/alerts.yml").read().count("- alert: ")
          and "QueueLensBrokerDown" in y and exprs_ok, [r["name"] for r in rules])
    check(19, "Alert-delivery outcomes exported as a Prometheus metric", "deliver" in m2,
          "no delivery metric exists — outcomes live only in the notifications table", bad="FAIL")
    files = os.listdir(f"{REPO}/deploy/prometheus")
    check(19, "Deployment-ready Prometheus config shipped", "prometheus.yml" in files,
          f"deploy/prometheus has {files}; the scrape_config is only a copy-paste snippet in the Metrics screen", bad="NOTE")


# ================================================================== G10/11 environments + vhosts
async def g10():
    envs = {e["id"]: e for e in (await api("GET", "/api/environments")).json()["environments"]}
    check(10, "Environments from config listed (default + staging + broken)",
          {"development", "staging", "broken"} <= set(envs) and envs["staging"]["vhosts"] == ["/", "ql-staging"],
          {k: v["vhosts"] for k, v in envs.items()})
    await api("GET", "/api/topology")  # warm the default scope's cache
    stg_scope = {"X-QueueLens-Environment": "staging", "X-QueueLens-Vhost": "ql-staging"}
    r = await api("POST", "/api/environments/activate", auth=OPSENV, json={"environment": "staging", "vhost": "ql-staging"})
    broker = (await api("GET", "/api/broker", auth=OPSENV, headers=stg_scope)).json()
    async with httpx.AsyncClient(auth=MAUTH) as m:
        vh = (await m.get(f"{MGMT}/api/vhosts/ql-staging")).status_code
    check(10, "Operator switches environment + vhost (vhost created on first use)",
          r.status_code == 200 and broker["environment"] == "staging" and broker["vhost"] == "ql-staging" and vh == 200,
          (r.status_code, broker))
    other = (await api("GET", "/api/broker", auth=ADMIN)).json()
    check(10, "A switch is per client — everyone else keeps their environment",
          (other["environment"], other["vhost"]) == ("development", "/"), other)
    stg = await aio_pika.connect_robust(amqp_url("ql-staging"))
    await decl("t10.staging.dlq", conn=stg)
    await pub("t10.staging.dlq", {"env": "staging"}, conn=stg)
    staged = {q["name"] for q in (await api("GET", "/api/queues", headers=stg_scope)).json()["queues"]}
    default = {q["name"] for q in (await api("GET", "/api/queues")).json()["queues"]}
    check(11, "Two vhosts browsed at once — each request sees only its own",
          "t10.staging.dlq" in staged and "t1.orders.dlq" not in staged
          and "t1.orders.dlq" in default and "t10.staging.dlq" not in default)
    rule = (await api("POST", "/api/alerts", headers=stg_scope, json={
        "name": "t10 staged", "pattern": "t10.staging.dlq", "metric": "messages", "operator": ">=",
        "threshold": 1, "severity": "Info"})).json()
    async def staged_note():
        return [n for n in await notifications() if n["title"] == "Rule fired: t10 staged"]

    note = await until(staged_note, 15)
    await api("DELETE", f"/api/alerts/{rule.get('id')}")
    check(15, "An alert rule watches the environment and vhost it was created in",
          (rule.get("environment"), rule.get("vhost")) == ("staging", "ql-staging")
          and note and "in staging · ql-staging" in note[0]["message"],
          (rule.get("environment"), rule.get("vhost"), note and note[0]["message"]))
    r = await api("GET", f"/api/queues/{quote('t10.staging.dlq', safe='')}/messages", headers=stg_scope)
    msgs = r.json().get("messages", []) if r.status_code == 200 else []
    check(11, "Preview works in the scoped vhost", msgs and msgs[0]["payload"] == {"env": "staging"})
    topo = {q["name"] for q in (await api("GET", "/api/topology", headers=stg_scope)).json()["queues"]}
    check(10, "Topology is per scope (never the other vhost's cached snapshot)",
          "t10.staging.dlq" in topo and "t1.orders.dlq" not in topo)
    dry = (await api("POST", "/api/messages/bulk/dry-run", headers=stg_scope,
                     json={"source_queue": "t10.staging.dlq", "action": "park"})).json()
    r = await api("POST", "/api/messages/bulk/execute", json={"batch_id": dry.get("batch_id"), "confirm": True})
    check(7, "A dry run executes only where it scanned (staging batch refused on the default)",
          r.status_code == 404 and "made against staging" in r.text and await count("t10.staging.dlq", conn=stg) == 1,
          (r.status_code, r.text[:160]))
    r = await api("POST", "/api/messages/park", auth=OPSENV, headers=stg_scope,
                  json={"source_queue": "t10.staging.dlq", "fingerprint": msgs[0]["fingerprint"] if msgs else "", "confirm": True})
    parked = [e for e in await audit(action="park") if e["source_queue"] == "t10.staging.dlq"]
    check(13, "Audit rows name the environment and vhost they acted in",
          r.status_code == 200 and parked and all(
              (e["metadata"].get("environment"), e["metadata"].get("vhost")) == ("staging", "ql-staging") for e in parked),
          [e["metadata"] for e in parked][:2])
    a = await audit(action="switch_environment")
    n = [x for x in await notifications() if "switched" in x["title"].lower()]
    check(10, "Switch audited; nobody else is notified (nothing changed for them)",
          a and a[0]["username"] == "opsenv" and not n)
    r = await api("POST", "/api/environments/activate", auth=OPSENV, json={"environment": "staging", "vhost": "t10-rogue-vhost"})
    h = await api("GET", "/api/queues", auth=OPSENV,
                  headers={"X-QueueLens-Environment": "staging", "X-QueueLens-Vhost": "t10-rogue-vhost"})
    async with httpx.AsyncClient(auth=MAUTH) as m:
        rogue = (await m.get(f"{MGMT}/api/vhosts/t10-rogue-vhost")).status_code
    check(10, "Operator cannot create arbitrary broker vhosts (switch or header)",
          r.status_code == 404 and h.status_code == 404 and rogue == 404, (r.status_code, h.status_code, rogue))
    r = await api("POST", "/api/environments/activate", json={"environment": "broken"})
    check(10, "Unreachable/bad-credential env → 502", r.status_code == 502, r.status_code)
    check(10, "Unknown env → 404 (switch and header)",
          (await api("POST", "/api/environments/activate", json={"environment": "nope"})).status_code == 404
          and (await api("GET", "/api/queues", headers={"X-QueueLens-Environment": "nope"})).status_code == 404)

    r = await api("POST", "/api/environments", json={"name": "t10env", "vhosts": ["/"]})
    r2 = await api("POST", "/api/environments", json={"name": "t10ext", "vhosts": ["/"], "host": f"{BROKER}:{AMQP_PORT}",
                                                      "username": MAUTH[0], "password": MAUTH[1],
                                                      "management_url": MGMT})
    ev = {e["metadata"]["name"]: e["username"] for e in await audit(action="add_environment")}
    check(10, "Admin adds environments (same broker + own credentials)", r.status_code == 200 and r2.status_code == 200)
    check(10, "add_environment audit names the acting admin", ev.get("t10ext") == "admin",
          f"audit username for t10ext = {ev.get('t10ext')!r} (the AMQP username overwrote the actor)")
    s = (await api("GET", "/api/settings", auth=USERS["viewer1"])).json()["custom_environments"]["t10ext"]
    check(21, "Stored environment credentials redacted in API responses",
          s.get("rabbitmq_url") == "__redacted__" and s.get("management_password") == "__secret__", s)
    rows = dict(db_rows("select key, value from app_settings"))
    check(21, "Environment credentials encrypted at rest", "__encrypted__" in rows.get("custom_environments", ""))
    codes = ((await api("DELETE", "/api/environments/staging")).status_code,
             (await api("DELETE", "/api/environments/t10env")).status_code)
    check(10, "Remove: env-var env refused (404), custom env removed", codes == (404, 200), codes)
    ext = {"X-QueueLens-Environment": "t10ext"}
    used = await api("GET", "/api/queues", headers=ext)  # opens its own connection
    d = await api("DELETE", "/api/environments/t10ext")
    after = await api("GET", "/api/queues", headers=ext)
    check(10, "Removing an environment in use closes it; requests naming it then 404",
          (used.status_code, d.status_code, after.status_code) == (200, 200, 404),
          (used.status_code, d.status_code, after.status_code))
    async with stg.channel() as ch:
        await ch.queue_delete("t10.staging.dlq")
        await ch.queue_delete("t10.staging.dlq.parking")
    await stg.close()


# ================================================================== final phase: timers, restarts, rate limit
# ================================================================== G30 replay policies
POLICY = {}


async def g30_setup():
    """Dead letters now, run the policy minutes later (backoff 1 min): see g30."""
    await decl("t30.dlq")
    await decl("t30.work", {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": "t30.dlq"})
    for i in range(3):
        await pub("t30.work", {"policy": i}, message_id=f"t30-{i}")
    await deadletter("t30.work", 3)
    await wait_ql_count("t30.dlq", 3)
    body = {"name": "t30 work", "queue": "t30.dlq", "max_deaths": 3, "backoff_minutes": 1,
            "interval_minutes": 60, "cap": 100, "enabled": False}  # run by hand in g30 only
    denied = await api("POST", "/api/policies", json=body, auth=USERS["oper1"])
    created = await api("POST", "/api/policies", json=body)
    POLICY.update(created.json() if created.status_code == 200 else {})
    check(30, "Only an Admin creates a replay policy (Operator 403)",
          denied.status_code == 403 and created.status_code == 200, (denied.status_code, created.status_code))


async def g30():
    if not POLICY:
        check(30, "Replay policy exists", False, "g30_setup did not create it")
        return
    url = f"/api/policies/{POLICY['id']}"
    preview_r = (await api("POST", url + "/preview", auth=USERS["oper1"])).json()
    check(30, "Preview (Operator) shows the due messages and their origin, moving nothing",
          preview_r.get("targets") == {"t30.work": {"due": 3, "consumers": 0}} and await count("t30.dlq") == 3,
          preview_r)
    held = (await api("POST", url + "/run")).json()
    check(30, "A run holds back replays into a queue with no consumers",
          held.get("skipped_no_consumers") == 3 and held.get("replayed") == 0 and await count("t30.dlq") == 3, held)

    got: list[str] = []
    listener = await CONN.channel()
    queue = await listener.declare_queue("t30.work", passive=True)

    async def on_message(message):
        got.append(message.message_id)
        await message.ack()

    await queue.consume(on_message)
    async with httpx.AsyncClient(base_url=MGMT, auth=MAUTH, timeout=10) as m:
        await until(lambda: _consumers(m, "t30.work", 1), 30)
    ran = (await api("POST", url + "/run")).json()
    await until(lambda: _done(got, 3), 10)
    check(30, "A run replays due messages to the queue they died in (x-death)",
          ran.get("replayed") == 3 and sorted(got) == ["t30-0", "t30-1", "t30-2"], (ran, got))
    await listener.close()

    rows = (await api("GET", "/api/audit", params={"username": "policy:t30 work", "limit": 50})).json()["events"]
    check(30, "Every replayed message is audited as policy:<name>",
          sum(1 for r in rows if r["action"] == "replay" and r["result"] == "success") == 3, len(rows))

    for i in range(2):
        await pub("t30.work", {"exhausted": i}, message_id=f"t30-x{i}")
    await deadletter("t30.work", 2)
    await wait_ql_count("t30.dlq", 2)
    await api("PUT", url, json={"name": "t30 work", "queue": "t30.dlq", "max_deaths": 1, "backoff_minutes": 1,
                                "interval_minutes": 60, "cap": 100, "enabled": False})
    parked = (await api("POST", url + "/run")).json()
    check(30, "A run parks messages at the death limit",
          parked.get("parked") == 2 and await count("t30.dlq.parking") == 2, parked)

    codes = ((await api("PATCH", url, json={"enabled": True}, auth=USERS["oper1"])).status_code,
             (await api("PATCH", url, json={"enabled": True})).status_code,
             (await api("PATCH", url, json={"enabled": False}, auth=USERS["oper1"])).status_code)
    check(30, "Operators pause a policy; only an Admin turns one back on", codes == (403, 200, 200), codes)
    metrics = (await api("GET", "/metrics")).text
    check(30, "/metrics counts what policies did",
          'queuelens_policy_messages_total{outcome="replayed",policy="t30 work"} 3.0' in metrics
          and 'queuelens_policy_paused{policy="t30 work"} 0.0' in metrics,
          [line for line in metrics.splitlines() if "policy" in line and not line.startswith("#")][:6])

    # the same queue names in two vhosts: a policy made in staging acts there only
    stg = await aio_pika.connect_robust(amqp_url("ql-staging"))
    try:
        for conn in (stg, CONN):
            await decl("t30.scoped.dlq", conn=conn)
            await decl("t30.scoped.work", {"x-dead-letter-exchange": "",
                                           "x-dead-letter-routing-key": "t30.scoped.dlq"}, conn=conn)
            await pub("t30.scoped.work", {"scoped": True}, conn=conn)
            await deadletter("t30.scoped.work", 1, conn=conn)
            await until(lambda conn=conn: _eq(count("t30.scoped.dlq", conn=conn), 1), 10)
        staged = {"X-QueueLens-Environment": "staging", "X-QueueLens-Vhost": "ql-staging"}
        made = (await api("POST", "/api/policies", headers=staged, json={
            "name": "t30 scoped", "queue": "t30.scoped.dlq", "max_deaths": 1, "backoff_minutes": 1,
            "interval_minutes": 60, "cap": 100, "enabled": False})).json()
        ran = (await api("POST", f"/api/policies/{made.get('id')}/run")).json()  # from the default scope
        check(30, "A policy acts only in the environment and vhost it was created in",
              (made.get("environment"), made.get("vhost")) == ("staging", "ql-staging")
              and ran.get("parked") == 1 and await count("t30.scoped.dlq.parking", conn=stg) == 1
              and await count("t30.scoped.dlq") == 1 and await count("t30.scoped.dlq.parking") is None,
              (made.get("environment"), made.get("vhost"), ran))
    finally:
        await stg.close()


async def _consumers(mgmt, queue, want):
    return (await mgmt.get(f"/api/queues/%2F/{queue}")).json().get("consumers") == want


async def _done(got, want):
    return len(got) >= want


async def final():
    wait = 32 - (time.monotonic() - min(EXPIRY_TOKEN["t"], TOPO["t0"]))
    if wait > 0:
        await asyncio.sleep(wait)
    r = await api("POST", "/api/messages/bulk/execute", json={"batch_id": EXPIRY_TOKEN["id"], "confirm": True})
    check(7, "Dry-run token expires (TTL 30s)", r.status_code == 404, r.status_code)
    await asyncio.sleep(max(0, 31 - (time.monotonic() - TOPO["t0"])))
    t = (await api("GET", "/api/topology")).json()
    check(9, "Topology cache refreshes after 30s", "t9.new" in {q["name"] for q in t["queues"]})

    # restart-durable state
    await decl("t29.persist")
    await pub("t29.persist", {"p": 1})
    rule = (await api("POST", "/api/alerts", json={"name": "t29 persist", "pattern": "t29.persist", "metric": "messages",
                                                   "threshold": 0, "severity": "Info"})).json()
    await until(lambda: _fired_count("Rule fired: t29 persist"), 25)
    for i in range(2):
        await pub("t7.dlq", {"restart": i})
    dr = (await api("POST", "/api/messages/bulk/dry-run", json={"source_queue": "t7.dlq", "action": "park",
                                                                  "payload_contains": "restart"})).json()
    db_exec("insert into audit_events (timestamp, username, action, result, metadata_json) "
            "values ('2020-01-01 00:00:00.000000', 'old', 'replay', 'success', '{}')")
    await put_settings({"retention": {"days": 30}})
    stop_server()
    started = start_server()
    check(26, "Server restarts cleanly", started)
    await asyncio.sleep(3)
    old = db_rows("select count(*) from audit_events where username='old'")[0][0]
    check(13, "Retention purges audit rows older than N days (runs at boot)", old == 0, f"old rows left: {old}")
    check(15, "Fired state survives restart (no duplicate notification)",
          await _fired_count("Rule fired: t29 persist") == 1)
    ex = await api("POST", "/api/messages/bulk/execute", json={"batch_id": dr["batch_id"], "confirm": True})
    check(7, "Dry-run token survives a restart", ex.status_code == 200,
          f"HTTP {ex.status_code} — docs/API.md says tokens 'live in process memory (a restart voids them)'; "
          "the code persists them in bulk_batches", bad="NOTE")
    await api("DELETE", f"/api/alerts/{rule['id']}")

    stop_server()
    booted = start_server(timeout=12, QUEUELENS_SECRET_KEY="")
    log_tail = open(LOG).read().rsplit("===== start", 1)[-1]
    check(21, "Boot without the Fernet key fails closed (encrypted settings unreadable)",
          not booted and "QUEUELENS_SECRET_KEY is not configured" in log_tail,
          "app refuses to start" if not booted else "app booted without the key")
    check(26, "Startup failure reports the real cause as the final error",
          "UnboundLocalError" not in log_tail,
          "lifespan's finally touches retention_task before it exists → the last traceback line is "
          "UnboundLocalError, the real ValueError is buried above it")
    stop_server()
    start_server()

    # timing + PBKDF2 cost
    async def timed(auth, n=3):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            await api("GET", "/api/me", auth=auth)
            ts.append(time.perf_counter() - t0)
        return sorted(ts)[n // 2]

    env_ok = await timed(ADMIN)
    db_ok = await timed(USERS["viewer1"])
    check(12, "DB-user requests don't re-run PBKDF2 each time (verified-login cache)", db_ok < 0.015,
          f"env user {env_ok * 1000:.0f} ms vs DB user {db_ok * 1000:.0f} ms per request (median of 3; PBKDF2 alone is ~30 ms)")
    async def interleaved(a, b, n=7):
        """Request times for two credentials, sampled alternately (14 failed logins, under
        the per-account and per-IP limits the next checks rely on)."""
        times = ([], [])
        for _ in range(n):
            for auth, ts in zip((a, b), times, strict=True):
                t0 = time.perf_counter()
                await api("GET", "/api/me", auth=auth)
                ts.append(time.perf_counter() - t0)
        return times

    missing, wrongpw = await interleaved(("no-such-user", token_urlsafe(6)), ("oper1", token_urlsafe(6)))
    # the fastest of each is that path's own cost: a shared runner's load only ever adds
    # time, and comes in bursts that can sit on one account's samples (medians of 51 vs
    # 189 ms on CI, 36 vs 36 locally); an extra PBKDF2 would still show in the minimum
    fast = (min(missing), min(wrongpw))
    check(12, "Failed logins take the same time for unknown vs known usernames",
          abs(fast[1] - fast[0]) < 0.03,
          f"unknown user {fast[0] * 1000:.0f} ms vs known user {fast[1] * 1000:.0f} ms (fastest of 7; medians "
          f"{sorted(missing)[3] * 1000:.0f} / {sorted(wrongpw)[3] * 1000:.0f} ms) → username enumeration by timing",
          bad="FAIL")

    # rate limit last — it locks this IP out for 60s
    codes = []
    for _ in range(12):
        codes.append((await api("GET", "/api/me", auth=("admin", token_urlsafe(6)))).status_code)
        if codes[-1] == 429:
            break
    r = await api("GET", "/api/me", auth=ADMIN)
    check(12, "Failed-login throttling (429 + Retry-After) for the attacked account",
          429 in codes and r.status_code == 429 and r.headers.get("retry-after") == "60", f"codes {codes}")
    other = await api("GET", "/api/me", auth=OPSENV)
    check(12, "Other accounts behind the same IP keep working", other.status_code == 200,
          f"opsenv from the same IP → {other.status_code}")


async def app_dispatch_warning() -> dict:
    """Fire a Warning-severity rule once (test-channel always sends as Alert) and return
    the slack delivery outcome recorded on its notification."""
    # rule ids are reused after a delete, so match "new since now", not the id alone
    before = {n["id"] for n in await notifications()}
    rule = (await api("POST", "/api/alerts", json={"name": "t18 tz", "pattern": "t18.none", "metric": "messages",
                                                   "threshold": 0, "severity": "Warning", "channels": ["slack"]})).json()
    await decl("t18.none")
    await pub("t18.none", {"tz": 1})

    async def fired():
        return [n for n in await notifications()
                if n["id"] not in before and n["title"] == "Rule fired: t18 tz"]

    got = await until(fired, 25)
    await api("DELETE", f"/api/alerts/{rule['id']}")
    await drain("t18.none")
    return (got[0]["delivery"].get("slack") or {}) if got else {"error": "rule never fired"}


async def wait_stats(name, timeout=20.0):
    """A queue's first Management API stats emission (seconds after declaration) — before it,
    counts and the applied policy are invisible and QueueLens refuses quorum queues."""
    async def ready():
        async with httpx.AsyncClient(base_url=MGMT, auth=MAUTH) as m:
            return "messages" in (await m.get(f"/api/queues/%2F/{quote(name, safe='')}")).json()
    return await until(ready, timeout)


async def _fired_count(title):
    return len([n for n in await notifications() if n["title"] == title])


# ================================================================== main
async def main():
    global CLIENT, CONN
    start_hook_server()
    await cleanup()
    assert start_server(), "server did not start: " + open(LOG).read()[-2000:]
    CLIENT = httpx.AsyncClient(base_url=BASE, timeout=60)
    CONN = await aio_pika.connect_robust(AMQP)
    groups = [g24, g12, g30_setup, g1, g2, g3, g4, g5, g6, g7, g8, g9, g13, g20, g21, g15_18, g19,
              g10, g30, final]
    try:
        for g in groups:
            print(f"\n--- {g.__name__} ---", flush=True)
            try:
                await g()
            except Exception as error:  # noqa: BLE001
                rec(0, f"{g.__name__} crashed", "ERROR", f"{error!r}\n{traceback.format_exc()[-600:]}")
    finally:
        await CONN.close()
        await CLIENT.aclose()
        stop_server()
    with open(RESULTS_FILE, "w") as f:
        json.dump(RESULTS, f, indent=1)
    tally: dict[str, int] = {}
    for r in RESULTS:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    print("\nTALLY", tally, "| results:", RESULTS_FILE, "| server log:", LOG)
    return 1 if tally.get("FAIL") or tally.get("ERROR") else 0


if __name__ == "__main__":
    if ENV("ACCEPTANCE") != "1":
        sys.exit("Set ACCEPTANCE=1 to run — it needs a disposable broker (see the module docstring).")
    sys.exit(asyncio.run(main()))
