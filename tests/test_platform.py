import httpx
import pytest

from app.config import Settings
from app.domain.models import QueueInfo
from app.main import create_app


def _app(tmp_path, **overrides):
    settings = Settings(
        auth_enabled=False,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
        **overrides,
    )
    return create_app(settings)


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

        def starttls(self):  # type: ignore[no-untyped-def]
            calls["starttls"] = True

        def login(self, username, password):  # type: ignore[no-untyped-def]
            calls["login"] = (username, password)

        def send_message(self, message):  # type: ignore[no-untyped-def]
            calls["sent"] = message["Subject"]

    original = mailer.smtplib.SMTP
    mailer.smtplib.SMTP = FakeSMTP  # type: ignore[misc]
    try:
        result = await mailer.send_email(
            {"smtp_host": "smtp.acme.io", "smtp_port": 587,
             "username": "apikey", "password": "sg-secret"},
            "hello", "body",
        )
    finally:
        mailer.smtplib.SMTP = original  # type: ignore[misc]

    assert result["ok"] is True
    assert calls["endpoint"] == ("smtp.acme.io", 587)
    assert calls["starttls"] is True  # implied by credentials on a non-465 port
    assert calls["login"] == ("apikey", "sg-secret")

    # the API never echoes the stored password back
    app = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/settings",
            json={"values": {"channels": {"email": {
                "smtp_host": "smtp.acme.io", "password": "sg-secret"}}}},
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
    assert "sg-secret" not in got.text
    assert stored["email"]["password"] == "sg-secret"
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
                  "username": "stg-user", "password": "stg-pass"},
        )
        listing = await client.get("/api/environments")
        settings = await client.get("/api/settings")
        removed = await client.delete("/api/environments/staging-2")
        gone = await client.get("/api/environments")
        not_removable = await client.delete("/api/environments/development")
    await app.state.database.close()

    assert created.status_code == 200
    env = next(e for e in listing.json()["environments"] if e["id"] == "staging-2")
    assert env["api"] == "http://rabbitmq-stg2:15672"
    assert env["removable"] is True
    # secrets never leave the server
    assert "stg-pass" not in listing.text
    assert "stg-pass" not in settings.text
    assert removed.status_code == 200
    assert all(e["id"] != "staging-2" for e in gone.json()["environments"])
    assert not_removable.status_code == 404
