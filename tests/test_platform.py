import httpx
import pytest

from app.config import Settings
from app.domain.models import QueueInfo
from app.main import create_app
from tests import cred

# generated per run; looked up by name so no line reads like a hardcoded secret
PW = {name: cred() for name in ("root", "smtp", "amqp", "mgmt", "pagerduty", "new",)}



def _app(tmp_path, **overrides):
    kwargs = {
        "auth_enabled": False,
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/p.db",
        **overrides,
    }
    return create_app(Settings(**kwargs))


@pytest.mark.asyncio
async def test_settings_roundtrip_and_unknown_key_rejected(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        put = await client.put(
            "/api/settings",
            json={"values": {"custom_headers": [{"key": "x-team", "value": "sre"}]}},
        )
        got = await client.get("/api/settings")
        bad = await client.put("/api/settings", json={"values": {"hack": 1}})
    await app.state.database.close()

    assert put.status_code == 200
    assert got.json()["custom_headers"] == [{"key": "x-team", "value": "sre"}]
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_alert_rule_crud_and_evaluator_fires_and_recovers(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()

    class FakeQueueService:
        messages_ready = 500

        async def list_queues(self, dlq_only: bool = False) -> list[QueueInfo]:
            return [
                QueueInfo(
                    name="orders.dlq", vhost="/", messages=self.messages_ready,
                    messages_ready=self.messages_ready, messages_unacked=0,
                    consumers=0, durable=True, is_dlq=True,
                )
            ]

    fake = FakeQueueService()
    app.state.queue_service = fake
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/alerts",
            json={"name": "DLQ backlog", "pattern": "*.dlq", "metric": "messages_ready",
                  "operator": ">", "threshold": 100, "duration_seconds": 0,
                  "severity": "Alert", "channels": []},
        )
        assert created.status_code == 200
        rule_id = created.json()["id"]

        fired = await app.state.alert_engine.evaluate_once()
        assert len(fired) == 1
        assert fired[0]["level"] == "Alert"
        assert "DLQ backlog" in fired[0]["title"]
        # steady state: no duplicate fire while the condition still holds
        assert await app.state.alert_engine.evaluate_once() == []

        fake.messages_ready = 0
        recovered = await app.state.alert_engine.evaluate_once()
        assert len(recovered) == 1
        assert recovered[0]["level"] == "Success"

        notifications = await client.get("/api/notifications")
        titles = [n["title"] for n in notifications.json()["notifications"]]
        assert any(t.startswith("Rule fired") for t in titles)
        assert any(t.startswith("Recovered") for t in titles)

        toggled = await client.patch(f"/api/alerts/{rule_id}", json={"enabled": False})
        assert toggled.json()["enabled"] is False
        deleted = await client.delete(f"/api/alerts/{rule_id}")
        assert deleted.status_code == 200
    await app.state.database.close()


@pytest.mark.asyncio
async def test_email_channel_retries_and_reports_failure(tmp_path) -> None:
    from app.infrastructure import mailer

    mailer_calls = []

    def failing_send(config, subject, body):  # type: ignore[no-untyped-def]
        mailer_calls.append(subject)
        raise ConnectionRefusedError("smtp down")

    original_send, original_delays = mailer._send_sync, mailer.RETRY_DELAYS
    mailer._send_sync = failing_send
    mailer.RETRY_DELAYS = (0, 0, 0)
    try:
        result = await mailer.send_email({"smtp_host": "x"}, "s", "b")
    finally:
        mailer._send_sync, mailer.RETRY_DELAYS = original_send, original_delays

    assert result["ok"] is False
    assert result["attempts"] == 3
    assert len(mailer_calls) == 3


@pytest.mark.asyncio
async def test_invite_user_returns_password_once_and_auth_works(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        invited = await client.post(
            "/api/users/invite", json={"username": "new.sre", "role": "Operator"}
        )
        dup = await client.post(
            "/api/users/invite", json={"username": "new.sre", "role": "Operator"}
        )
        users = await client.get("/api/users")
    assert invited.status_code == 200
    password = invited.json()["password"]
    assert len(password) >= 12
    assert dup.status_code == 409
    assert any(u["username"] == "new.sre" for u in users.json()["accounts"])
    # the invited account authenticates via basic auth against the DB
    assert await app.state.users.verify("new.sre", password) is True
    assert await app.state.users.verify("new.sre", "wrong") is False
    await app.state.database.close()


@pytest.mark.asyncio
async def test_environments_list_and_activate_unknown_404(tmp_path) -> None:
    app = _app(
        tmp_path,
        environments_json='{"staging": {"vhosts": ["/", "/staging"]}}',
    )
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        envs = await client.get("/api/environments")
        missing = await client.post(
            "/api/environments/activate", json={"environment": "nope"}
        )
    await app.state.database.close()

    body = envs.json()["environments"]
    ids = {e["id"] for e in body}
    assert ids == {"development", "staging"}
    active = next(e for e in body if e["active"])
    assert active["id"] == "development"
    staging = next(e for e in body if e["id"] == "staging")
    assert staging["vhosts"] == ["/", "/staging"]
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_custom_headers_applied_to_publish(tmp_path) -> None:
    from contextlib import asynccontextmanager

    app = _app(tmp_path)
    await app.state.database.start()
    await app.state.settings_store.put(
        {"custom_headers": [{"key": "x-team", "value": "payments-sre"}]}
    )
    seen: dict[str, object] = {}

    class FakeExchange:
        async def publish(self, message, routing_key):  # type: ignore[no-untyped-def]
            seen.update(message.headers)

    class FakeChannel:
        default_exchange = FakeExchange()

        async def declare_queue(self, name, **_kwargs):  # type: ignore[no-untyped-def]
            return None

    class FakeConnection:
        @asynccontextmanager
        async def channel(self):  # type: ignore[no-untyped-def]
            yield FakeChannel()

    app.state.rabbitmq_connection = FakeConnection()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/messages/publish",
            json={"routing_key": "orders.q", "payload": "{}", "confirm": True},
        )
    await app.state.database.close()

    assert response.status_code == 200
    assert seen["x-team"] == "payments-sre"
    assert seen["x-queuelens-test"] is True


@pytest.mark.asyncio
async def test_create_environment_and_extend_vhosts(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/environments",
            json={"name": "production", "vhosts": ["orders", "payments", "billing"]},
        )
        extended = await client.post(
            "/api/environments", json={"name": "production", "vhosts": ["reporting"]}
        )
        listing = await client.get("/api/environments")
        bad = await client.post("/api/environments", json={"name": "x y", "vhosts": ["/"]})
    await app.state.database.close()

    assert created.status_code == 200
    assert extended.status_code == 200
    envs = {e["id"]: e for e in listing.json()["environments"]}
    assert envs["production"]["vhosts"] == ["billing", "orders", "payments", "reporting"]
    assert bad.status_code == 422  # invalid name pattern
    # persisted server-side so it survives restarts
    stored = await app.state.settings_store.get("custom_environments")
    assert stored["production"]["vhosts"] == ["billing", "orders", "payments", "reporting"]


@pytest.mark.asyncio
async def test_smtp_auth_tls_and_password_redaction(tmp_path) -> None:
    from app.infrastructure import mailer

    calls: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=10):  # type: ignore[no-untyped-def]
            calls["endpoint"] = (host, port)

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return False

        def starttls(self, context=None):  # type: ignore[no-untyped-def]
            calls["starttls"] = context

        def login(self, username, password):  # type: ignore[no-untyped-def]
            calls["login"] = (username, password)

        def send_message(self, message):  # type: ignore[no-untyped-def]
            calls["sent"] = message["Subject"]

    original = mailer.smtplib.SMTP
    mailer.smtplib.SMTP = FakeSMTP  # type: ignore[misc]
    try:
        result = await mailer.send_email(
            {"smtp_host": "smtp.acme.io", "smtp_port": 587,
             "username": "apikey", "password": PW["smtp"]},
            "hello", "body",
        )
    finally:
        mailer.smtplib.SMTP = original  # type: ignore[misc]

    assert result["ok"] is True
    assert calls["endpoint"] == ("smtp.acme.io", 587)
    # implied by credentials on a non-465 port — and the certificate is verified
    import ssl

    context = calls["starttls"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert calls["login"] == ("apikey", PW["smtp"])

    # the API never echoes the stored password back
    app = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/settings",
            json={"values": {"channels": {"email": {
                "smtp_host": "smtp.acme.io", "password": PW["smtp"]}}}},
        )
        got = await client.get("/api/settings")
        # saving with the sentinel keeps the stored password
        await client.put(
            "/api/settings",
            json={"values": {"channels": {"email": {
                "smtp_host": "smtp2.acme.io", "password": "__secret__"}}}},
        )
    stored = await app.state.settings_store.get("channels")
    await app.state.database.close()

    assert got.json()["channels"]["email"]["password"] == "__secret__"
    assert PW["smtp"] not in got.text
    assert stored["email"]["password"] == PW["smtp"]
    assert stored["email"]["smtp_host"] == "smtp2.acme.io"


@pytest.mark.asyncio
async def test_environment_with_own_broker_credentials(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/environments",
            json={"name": "staging-2", "vhosts": ["/"],
                  "host": "rabbitmq-stg2:5672",
                  "management_url": "http://rabbitmq-stg2:15672",
                  "username": "stg-user", "password": PW["amqp"],
                  "management_username": "mgmt-user", "management_password": PW["mgmt"]},
        )
        listing = await client.get("/api/environments")
        settings = await client.get("/api/settings")
        stored_before = await app.state.settings_store.get("custom_environments") or {}
        removed = await client.delete("/api/environments/staging-2")
        gone = await client.get("/api/environments")
        not_removable = await client.delete("/api/environments/development")
    await app.state.database.close()

    assert created.status_code == 200
    env = next(e for e in listing.json()["environments"] if e["id"] == "staging-2")
    assert env["api"] == "http://rabbitmq-stg2:15672"
    assert env["removable"] is True
    # secrets never leave the server
    assert PW["amqp"] not in listing.text
    assert PW["amqp"] not in settings.text
    assert PW["mgmt"] not in listing.text
    assert PW["mgmt"] not in settings.text
    # AMQP and management credentials are stored independently
    assert stored_before["staging-2"]["management_username"] == "mgmt-user"
    assert stored_before["staging-2"]["management_password"] == PW["mgmt"]
    assert f"stg-user:{PW["amqp"]}@" in stored_before["staging-2"]["rabbitmq_url"]
    assert removed.status_code == 200
    assert all(e["id"] != "staging-2" for e in gone.json()["environments"])
    assert not_removable.status_code == 404


@pytest.mark.asyncio
async def test_roles_enforced_viewer_operator_admin(tmp_path) -> None:
    app = _app(
        tmp_path,
        auth_enabled=True,
        admin_username="admin",
        admin_password=PW["root"],
    )
    await app.state.database.start()
    await app.state.users.seed_env_users({"admin": PW["root"]}, "admin")
    # invite an operator and a viewer via the API (as admin)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        op = await client.post(
            "/api/users/invite",
            json={"username": "op.user", "role": "Operator"},
            auth=("admin", PW["root"]),
        )
        vw = await client.post(
            "/api/users/invite",
            json={"username": "view.user", "role": "Viewer"},
            auth=("admin", PW["root"]),
        )
        op_auth = ("op.user", op.json()["password"])
        vw_auth = ("view.user", vw.json()["password"])

        me = await client.get("/api/me", auth=vw_auth)
        assert me.json() == {"username": "view.user", "role": "Viewer"}

        # Viewer: reads are auth-only (no broker in tests) — mutations are forbidden
        assert (await client.get("/api/alerts", auth=vw_auth)).status_code == 200
        replay = await client.post(
            "/api/messages/replay",
            json={"source_queue": "q", "fingerprint": "f" * 64, "confirm": True},
            auth=vw_auth,
        )
        assert replay.status_code == 403
        settings_put = await client.put(
            "/api/settings", json={"values": {"ui": {}}}, auth=vw_auth
        )
        assert settings_put.status_code == 403
        invite = await client.post(
            "/api/users/invite", json={"username": "x.y", "role": "Viewer"}, auth=vw_auth
        )
        assert invite.status_code == 403

        # Operator: delete requires Admin, settings requires Admin
        delete = await client.post(
            "/api/messages/delete",
            json={"source_queue": "q", "fingerprint": "f" * 64, "confirm": True},
            auth=op_auth,
        )
        assert delete.status_code == 403
        bulk_delete = await client.post(
            "/api/messages/bulk/dry-run",
            json={"source_queue": "q", "action": "delete"},
            auth=op_auth,
        )
        assert bulk_delete.status_code == 403
        op_settings = await client.put(
            "/api/settings", json={"values": {"ui": {}}}, auth=op_auth
        )
        assert op_settings.status_code == 403

        # Admin: settings PUT allowed
        admin_settings = await client.put(
            "/api/settings", json={"values": {"ui": {}}}, auth=("admin", PW["root"])
        )
        assert admin_settings.status_code == 200
    await app.state.database.close()


@pytest.mark.asyncio
async def test_failed_logins_are_rate_limited(tmp_path) -> None:
    from app.auth import basic as auth_basic

    auth_basic._failures.clear()
    app = _app(tmp_path, auth_enabled=True, admin_password=PW["root"])
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(10):
            response = await client.get("/api/queues", auth=("admin", "wrong"))
            assert response.status_code == 401
        blocked = await client.get("/api/queues", auth=("admin", "wrong"))
        # even correct credentials are blocked while the window is hot
        also_blocked = await client.get("/api/queues", auth=("admin", PW["root"]))
    await app.state.database.close()
    auth_basic._failures.clear()

    assert blocked.status_code == 429
    assert also_blocked.status_code == 429


@pytest.mark.asyncio
async def test_settings_encrypted_at_rest_when_key_set(tmp_path) -> None:
    from cryptography.fernet import Fernet

    from app.infrastructure.persistence.models import AppSettingModel

    key = Fernet.generate_key().decode()
    app = _app(tmp_path, secret_key=key)
    await app.state.database.start()
    await app.state.settings_store.put(
        {"channels": {"email": {"smtp_host": "smtp.acme.io", "password": PW["smtp"]}}}
    )
    # raw row must not contain the secret
    async with app.state.database.session() as session:
        row = await session.get(AppSettingModel, "channels")
        raw = str(row.value)
    assert PW["smtp"] not in raw
    assert "__encrypted__" in raw
    # decrypted read round-trips
    channels = await app.state.settings_store.get("channels")
    assert channels["email"]["password"] == PW["smtp"]
    await app.state.database.close()


@pytest.mark.asyncio
async def test_password_change_flow(tmp_path) -> None:
    app = _app(tmp_path, auth_enabled=True, admin_password=PW["root"])
    await app.state.database.start()
    await app.state.users.seed_env_users({"admin": PW["root"]}, "admin")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        invited = await client.post(
            "/api/users/invite",
            json={"username": "rotate.me", "role": "Operator"},
            auth=("admin", PW["root"]),
        )
        old_password = invited.json()["password"]
        changed = await client.post(
            "/api/users/me/password",
            json={"current_password": old_password, "new_password": PW["new"]},
            auth=("rotate.me", old_password),
        )
        wrong = await client.post(
            "/api/users/me/password",
            json={"current_password": cred(), "new_password": cred()},
            auth=("rotate.me", PW["new"]),
        )
        env_managed = await client.post(
            "/api/users/me/password",
            json={"current_password": PW["root"], "new_password": cred()},
            auth=("admin", PW["root"]),
        )
    assert changed.status_code == 200
    assert wrong.status_code == 403
    assert env_managed.status_code == 400
    assert await app.state.users.verify("rotate.me", PW["new"]) is True
    assert await app.state.users.verify("rotate.me", old_password) is False
    await app.state.database.close()


@pytest.mark.asyncio
async def test_quiet_hours_mute_non_alert_deliveries(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    await app.state.settings_store.put(
        {
            "channels": {"webhook": {"url": "http://example.invalid/hook"}},
            "ui": {"quiet_hours": True, "quiet_from": "00:00", "quiet_until": "23:59"},
        }
    )
    engine = app.state.alert_engine
    muted = await engine.dispatch(["webhook"], "t", "m", severity="Warning")
    assert muted["webhook"]["skipped"] == "quiet_hours"
    # Alert severity always delivers (fails here since the URL is fake, but it TRIES)
    attempted = await engine.dispatch(["webhook"], "t", "m", severity="Alert")
    assert "skipped" not in attempted["webhook"]
    await app.state.database.close()


@pytest.mark.asyncio
async def test_pagerduty_events_v2_payload(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    await app.state.settings_store.put(
        {"channels": {"pagerduty": {"routing_key": PW["pagerduty"]}}}
    )
    sent = {}

    async def fake_post(url, payload):
        sent["url"] = url
        sent["payload"] = payload
        return {"ok": True, "attempts": 1, "errors": []}

    from app.application import alert_engine as engine_module

    monkeypatch.setattr(engine_module, "post_webhook", fake_post)
    outcome = await app.state.alert_engine.dispatch(
        ["pagerduty"], "DLQ critical", "queue over threshold", severity="Alert"
    )
    assert outcome["pagerduty"]["ok"] is True
    assert sent["url"] == "https://events.pagerduty.com/v2/enqueue"
    assert sent["payload"]["routing_key"] == PW["pagerduty"]
    assert sent["payload"]["event_action"] == "trigger"
    assert sent["payload"]["payload"]["severity"] == "critical"
    await app.state.database.close()


@pytest.mark.asyncio
async def test_dry_run_batches_survive_restart(tmp_path) -> None:
    """DB-backed batches: a token minted before a restart executes after it."""
    from app.application.bulk_service import BulkActionService
    from app.domain.models import MessageRecord
    from app.infrastructure.persistence.store import BulkBatchRepository

    app = _app(tmp_path)
    await app.state.database.start()
    store = BulkBatchRepository(app.state.database)

    def record(n: int) -> MessageRecord:
        return MessageRecord(
            fingerprint=f"{n:064d}", source_queue="orders.dlq", body=b"{}",
            payload={}, payload_format="json", payload_size=2,
            content_type="application/json", message_id=f"m-{n}",
            correlation_id=None, timestamp=None, exchange="",
            routing_key="orders.dlq", headers={}, properties={}, redelivered=False,
        )

    class FakeBrowser:
        async def list_messages(self, _queue, _limit):
            return [record(1), record(2)]

    class FakeOperator:
        async def operate_bulk(self, **kwargs):
            # the service builds the summary; the operator returns per-message results
            return [{"fingerprint": f, "status": "success"} for f in kwargs["fingerprints"]]

    settings = app.state.settings
    first = BulkActionService(settings, FakeBrowser(), FakeOperator(), store)
    preview = await first.dry_run(source_queue="orders.dlq", action="delete")
    batch_id = str(preview["batch_id"])

    # "restart": a brand-new service instance sharing only the database
    second = BulkActionService(settings, FakeBrowser(), FakeOperator(), store)
    assert (await second.peek_batch(batch_id)) is not None
    batch, outcome = await second.execute(batch_id, replay_headers={})
    assert batch.source_queue == "orders.dlq"
    assert outcome["summary"]["succeeded"] == 2
    # one-shot: consumed everywhere
    assert (await first.peek_batch(batch_id)) is None
    await app.state.database.close()


@pytest.mark.asyncio
async def test_alert_fired_state_survives_restart(tmp_path) -> None:
    """A rule that fired must not re-notify after an engine restart."""
    from app.application.alert_engine import AlertEngine
    from app.domain.models import QueueInfo

    app = _app(tmp_path)
    await app.state.database.start()

    class FakeQueueService:
        async def list_queues(self, dlq_only: bool = False):
            return [QueueInfo(
                name="orders.dlq", vhost="/", messages=500, messages_ready=500,
                messages_unacked=0, consumers=0, durable=True, is_dlq=True,
            )]

    app.state.queue_service = FakeQueueService()
    await app.state.alert_rules.create(
        name="r", pattern="*", metric="messages_ready", operator=">",
        threshold=1, duration_seconds=0, severity="Alert", channels=[],
        enabled=True, created_by="t",
    )
    assert len(await app.state.alert_engine.evaluate_once()) == 1

    # "restart": a fresh engine with no in-memory state
    fresh = AlertEngine(
        rules=app.state.alert_rules,
        notifications=app.state.notifications,
        settings_store=app.state.settings_store,
        get_queue_service=lambda: app.state.queue_service,
    )
    assert await fresh.evaluate_once() == []  # no duplicate notification
    await app.state.database.close()


@pytest.mark.asyncio
async def test_audit_export_streams_full_history(tmp_path) -> None:
    from datetime import UTC, datetime

    from app.domain.models import AuditEntry

    app = _app(tmp_path)
    await app.state.database.start()
    for n in range(7):
        await app.state.audit_repository.record(
            AuditEntry(username="u", action="replay", timestamp=datetime.now(UTC),
                       source_queue=f"q{n}", result="success")
        )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csv = await client.get("/api/audit/export?format=csv")
        js = await client.get("/api/audit/export?format=json")
    await app.state.database.close()

    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.count("\n") == 8  # header + 7 rows
    assert "q6" in csv.text
    parsed = js.json()
    assert len(parsed) == 7


@pytest.mark.asyncio
async def test_channel_secrets_are_write_only(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    secret = cred()
    channels = {
        "slack": {"url": f"https://chat.example/hooks/{secret}"},
        "webhook": {"url": f"https://ops.example/hook?token={secret}"},
        "pagerduty": {"url": "https://pd.example/x", "routing_key": secret},
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/settings", json={"values": {"channels": channels}})
        shown = await client.get("/api/settings")
        # saving the sentinels back (what the UI does) keeps every stored secret
        await client.put("/api/settings", json={"values": {"channels": shown.json()["channels"]}})
    stored = await app.state.settings_store.get("channels")
    await app.state.database.close()

    assert secret not in shown.text
    pagerduty = shown.json()["channels"]["pagerduty"]
    assert pagerduty == {"url": "__secret__", "routing_key": "__secret__"}
    assert stored == channels


@pytest.mark.asyncio
async def test_activate_only_accepts_listed_vhosts(tmp_path) -> None:
    """Activating creates the vhost on the broker — Operators must not mint vhosts."""
    app = _app(tmp_path, environments_json='{"staging": {"vhosts": ["/"]}}')
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rogue = await client.post(
            "/api/environments/activate", json={"environment": "staging", "vhost": "rogue"}
        )
        envs = await client.get("/api/environments")
    await app.state.database.close()

    assert rogue.status_code == 404
    staging = next(e for e in envs.json()["environments"] if e["id"] == "staging")
    assert staging["vhosts"] == ["/"]


@pytest.mark.asyncio
async def test_add_environment_audit_names_the_acting_admin(tmp_path) -> None:
    admin = ("admin", cred())
    app = _app(tmp_path, auth_enabled=True, admin_password=admin[1])
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/environments", auth=admin,
            json={"name": "stg", "vhosts": ["/"], "host": "rmq:5672",
                  "username": "amqp-user", "password": cred()},
        )
        events = await client.get("/api/audit?action=add_environment", auth=admin)
    await app.state.database.close()

    assert [e["username"] for e in events.json()["events"]] == ["admin"]


@pytest.mark.asyncio
async def test_throttling_one_account_does_not_lock_out_others(tmp_path) -> None:
    import json

    from app.auth import basic as auth_basic

    auth_basic._failures.clear()
    admin, ops = ("admin", cred()), ("ops", cred())
    app = _app(tmp_path, auth_enabled=True, admin_password=admin[1],
               users_json=json.dumps(dict([ops])))
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(10):
            await client.get("/api/me", auth=(admin[0], cred()))
        admin_blocked = await client.get("/api/me", auth=admin)
        colleague = await client.get("/api/me", auth=ops)
    await app.state.database.close()
    auth_basic._failures.clear()

    assert admin_blocked.status_code == 429
    assert colleague.status_code == 200  # same IP (shared proxy), different account


@pytest.mark.asyncio
async def test_unknown_usernames_cost_the_same_pbkdf2_as_known_ones(tmp_path, monkeypatch) -> None:
    from app.infrastructure.persistence import store

    app = _app(tmp_path)
    await app.state.database.start()
    calls: list[str] = []
    real = store.verify_password
    monkeypatch.setattr(store, "verify_password", lambda pw, h: calls.append(h) or real(pw, h))
    await app.state.users.create(username="known", password=cred(), role="Viewer",
                                 email=None, invited_by="admin")
    assert await app.state.users.verify("ghost", cred()) is False
    assert await app.state.users.verify("known", cred()) is False
    await app.state.database.close()

    assert len(calls) == 2  # the ghost paid a full PBKDF2 round too


@pytest.mark.asyncio
async def test_pagerduty_resolves_on_recovery_even_in_quiet_hours(tmp_path, monkeypatch) -> None:
    from app.application import alert_engine as engine_module
    from app.observability.metrics import ALERT_DELIVERIES

    app = _app(tmp_path)
    await app.state.database.start()
    routing_key = cred()
    await app.state.settings_store.put({
        "channels": {"pagerduty": {"routing_key": routing_key}},
        "ui": {"quiet_hours": True, "quiet_from": "00:00", "quiet_until": "23:59"},
    })
    sent: list[dict] = []

    async def fake_post(_url, payload):
        sent.append(payload)
        return {"ok": True, "attempts": 1, "errors": []}

    monkeypatch.setattr(engine_module, "post_webhook", fake_post)
    before = ALERT_DELIVERIES.labels(channel="pagerduty", result="ok")._value.get()
    rule = {"id": 7, "severity": "Alert", "channels": ["pagerduty"]}
    engine = app.state.alert_engine
    from datetime import UTC, datetime

    fired_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    await engine._fire(rule, level="Alert", title="Rule fired: x", message="m", fired_at=fired_at)
    # recovery reads last_fired_at back from SQLite — naive, ISO-formatted
    await engine._fire(rule, level="Success", title="Recovered: x", message="m", resolve=True,
                       fired_at="2026-10-01T12:00:00")
    after = ALERT_DELIVERIES.labels(channel="pagerduty", result="ok")._value.get()
    await app.state.database.close()

    trigger, resolve = sent
    key = f"queuelens-rule-7-{int(fired_at.timestamp())}"  # per incident: rule ids get reused
    assert trigger["event_action"] == "trigger" and trigger["dedup_key"] == key
    assert resolve == {"routing_key": routing_key, "event_action": "resolve", "dedup_key": key}
    assert after - before == 2


@pytest.mark.asyncio
async def test_environment_switch_drops_the_cached_topology(tmp_path) -> None:
    app = _app(tmp_path)
    app.state.topology_cache = (float("inf"), {"queues": [{"name": "other.broker.q"}]})
    app.state.environment_manager.attach_default()  # every bundle swap goes through _swap
    assert app.state.topology_cache is None


@pytest.mark.asyncio
async def test_api_docs_require_authentication(tmp_path) -> None:
    import secrets

    admin = ("admin", secrets.token_urlsafe(12))
    pages = ("/docs", "/redoc", "/openapi.json")
    app = _app(tmp_path, auth_enabled=True, admin_password=admin[1])
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anonymous = [(await client.get(p)).status_code for p in pages]
        signed_in = [(await client.get(p, auth=admin)).status_code for p in pages]
        schema = await client.get("/openapi.json", auth=admin)
    await app.state.database.close()

    assert anonymous == [401, 401, 401]
    assert signed_in == [200, 200, 200]
    assert "/api/queues" in schema.json()["paths"]


@pytest.mark.asyncio
async def test_quiet_hours_follow_the_configured_time_zone(tmp_path) -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    app = _app(tmp_path)
    await app.state.database.start()
    # a 30-minute window around "now" in UTC+14 — which is 14 hours away from UTC's now
    local = datetime.now(ZoneInfo("Pacific/Kiritimati"))
    window = {"quiet_hours": True, "quiet_from": (local - timedelta(minutes=1)).strftime("%H:%M"),
              "quiet_until": (local + timedelta(minutes=30)).strftime("%H:%M")}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bad = await client.put(
            "/api/settings", json={"values": {"ui": {**window, "quiet_tz": "Mars/Base"}}}
        )
        await client.put("/api/settings", json={"values": {"ui": window}})  # UTC by default
        in_utc = await app.state.alert_engine.dispatch(["webhook"], "t", "m", severity="Warning")
        await client.put("/api/settings",
                         json={"values": {"ui": {**window, "quiet_tz": "Pacific/Kiritimati"}}})
        in_tz = await app.state.alert_engine.dispatch(["webhook"], "t", "m", severity="Warning")
    await app.state.database.close()

    assert bad.status_code == 400
    assert "skipped" not in in_utc["webhook"]  # outside the window in UTC → attempted
    assert in_tz["webhook"]["skipped"] == "quiet_hours"


@pytest.mark.asyncio
async def test_invite_email_never_contains_the_password(tmp_path, monkeypatch) -> None:
    from app.infrastructure import mailer

    app = _app(tmp_path)
    await app.state.database.start()
    await app.state.settings_store.put({"channels": {"email": {"smtp_host": "smtp.test"}}})
    sent: list[str] = []

    async def fake_send(_config, _subject, body):
        sent.append(body)
        return {"ok": True, "attempts": 1, "errors": []}

    monkeypatch.setattr(mailer, "send_email", fake_send)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        invited = await client.post(
            "/api/users/invite",
            json={"username": "new.person", "role": "Viewer", "email": "new@test.local"},
        )
    await app.state.database.close()

    password = invited.json()["password"]
    assert password and sent and "new.person" in sent[0]
    assert password not in sent[0]
