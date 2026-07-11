import httpx
import pytest

from app.config import Settings
from app.domain.models import MessageRecord, QueueInfo
from app.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/login", "/classic", "/queues", "/queues/orders.dlq", "/messages",
        "/messages/orders.dlq/abc", "/messages/orders.dlq/abc/replay",
        "/replay", "/users", "/notifications", "/audit", "/config",
    ],
)
async def test_legacy_console_paths_redirect_to_spa(path) -> None:
    """The server-rendered console was retired — every old page lands on /app."""
    app = create_app(Settings(auth_enabled=False))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, follow_redirects=False)

    assert response.status_code == 301
    assert response.headers["location"] == "/app"


@pytest.mark.asyncio
async def test_ambiguous_message_detail_returns_404() -> None:
    from app.application.message_service import MessageNotUniquelyIdentifiable

    app = create_app(Settings(auth_enabled=False))

    class FakeMessageService:
        async def get_message(self, queue: str, fingerprint: str, _limit: int) -> MessageRecord:
            raise MessageNotUniquelyIdentifiable(
                f"Message {fingerprint} was not found uniquely in {queue}"
            )

    app.state.message_service = FakeMessageService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/queues/orders.dlq/messages/{'c' * 64}")

    assert response.status_code == 404
    assert "not found uniquely" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unknown_queue_returns_404_not_500() -> None:
    from app.infrastructure.rabbitmq.management_client import RabbitMQManagementError

    app = create_app(Settings(auth_enabled=False))

    class FakeQueueService:
        async def get_queue(self, _queue_name: str) -> QueueInfo:
            raise RabbitMQManagementError(
                "RabbitMQ Management API returned HTTP 404", status_code=404
            )

    app.state.queue_service = FakeQueueService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        api = await client.get("/api/queues/does.not.exist")

    assert api.status_code == 404
    assert api.json() == {"detail": "Queue not found"}


@pytest.mark.asyncio
async def test_multi_user_auth_enforced_on_api(tmp_path) -> None:
    import json as jsonlib

    app = create_app(
        Settings(
            auth_enabled=True,
            admin_username="admin",
            admin_password="root-pw",
            users_json=jsonlib.dumps({"sre": "sre-pw"}),
            database_url=f"sqlite+aiosqlite:///{tmp_path}/u.db",
        )
    )
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anonymous = await client.get("/api/me")
        wrong = await client.get("/api/me", auth=("sre", "bad"))
        sre = await client.get("/api/me", auth=("sre", "sre-pw"))
        admin = await client.get("/api/me", auth=("admin", "root-pw"))
    await app.state.database.close()

    assert anonymous.status_code == 401
    assert wrong.status_code == 401
    assert sre.status_code == 200
    assert admin.status_code == 200
    assert admin.json()["role"] == "Admin"
    assert sre.json()["username"] == "sre"


@pytest.mark.asyncio
async def test_spa_route_and_users_api(tmp_path) -> None:
    app = create_app(
        Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/spa.db")
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        spa = await client.get("/app")
        root = await client.get("/", follow_redirects=False)
        users_api = await client.get("/api/users")
        kit_index = await client.get("/static/ds/ui_kits/queuelens/index.html")
        loader = await client.get("/static/ds/ds-loader.js")

    assert spa.status_code == 200
    assert "/static/ds/ui_kits/queuelens/data.js" in spa.text
    assert root.status_code == 307
    assert root.headers["location"] == "/app"
    assert users_api.status_code == 200
    assert users_api.json()["accounts"][0]["role"] in ("Administrator", "Operator")
    assert kit_index.status_code == 200
    assert "data.js" in kit_index.text
    assert loader.status_code == 200


@pytest.mark.asyncio
async def test_config_api_is_read_only_and_never_leaks_secrets(tmp_path) -> None:
    app = create_app(
        Settings(
            auth_enabled=False,
            admin_password="super-secret-pw",
            rabbitmq_management_password="mgmt-secret",
            database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["max_preview_messages"] == 100
    assert body["max_bulk_size"] == 500
    assert body["masking_enabled"] is True
    assert "password" in body["masked_fields"]
    assert "super-secret-pw" not in response.text
    assert "mgmt-secret" not in response.text


@pytest.mark.asyncio
async def test_exchanges_api_hides_internal_exchanges(tmp_path) -> None:
    app = create_app(
        Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/e.db")
    )

    class FakeManagementClient:
        async def list_exchanges(self) -> list[dict[str, object]]:
            return [
                {"name": "", "type": "direct"},
                {"name": "amq.topic", "type": "topic", "internal": False},
                {"name": "amq.rabbitmq.trace", "type": "topic", "internal": True},
            ]

    app.state.management_client = FakeManagementClient()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/exchanges")

    assert response.status_code == 200
    names = [e["name"] for e in response.json()["exchanges"]]
    assert "amq.topic" in names
    assert "amq.rabbitmq.trace" not in names


@pytest.mark.asyncio
async def test_topology_and_alert_rules_endpoints(tmp_path) -> None:
    app = create_app(
        Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db")
    )

    class FakeManagementClient:
        async def list_exchanges(self) -> list[dict[str, object]]:
            return [
                {"name": "", "type": "direct"},
                {"name": "orders.exchange", "type": "topic"},
                {"name": "amq.rabbitmq.trace", "type": "topic", "internal": True},
            ]

        async def list_bindings(self) -> list[dict[str, object]]:
            return [
                {"source": "orders.exchange", "destination": "orders.q",
                 "destination_type": "queue", "routing_key": "orders.#"},
            ]

        async def list_queues(self) -> list[dict[str, object]]:
            return [
                {
                    "name": "orders.q", "consumers": 2, "messages": 5,
                    "arguments": {
                        "x-dead-letter-exchange": "",
                        "x-dead-letter-routing-key": "orders.dlq",
                    },
                },
            ]

    app.state.management_client = FakeManagementClient()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        topo = await client.get("/api/topology")
        rules = await client.get("/api/alert-rules")

    assert topo.status_code == 200
    body = topo.json()
    # internal + default exchanges are hidden
    assert [e["name"] for e in body["exchanges"]] == ["orders.exchange"]
    assert body["bindings"][0]["routing_key"] == "orders.#"
    assert body["queues"][0]["dlx_routing_key"] == "orders.dlq"
    assert rules.status_code == 200
    names = [r["name"] for r in rules.json()["rules"]]
    assert "QueueLensBrokerDown" in names
    assert all(r["severity"] in ("critical", "warning", "info") for r in rules.json()["rules"])


@pytest.mark.asyncio
async def test_publish_requires_confirm_and_audits_success(tmp_path) -> None:
    from contextlib import asynccontextmanager

    app = create_app(
        Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db")
    )
    await app.state.database.start()
    published: list[tuple[bytes, str]] = []

    class FakeExchange:
        async def publish(self, message: object, routing_key: str) -> None:
            published.append((message.body, routing_key))  # type: ignore[attr-defined]

    class FakeChannel:
        default_exchange = FakeExchange()

        async def declare_queue(self, name: str, **_kwargs: object) -> None:
            if name == "ghost.q":
                from aiormq.exceptions import ChannelNotFoundEntity

                raise ChannelNotFoundEntity(f"no queue '{name}'")

    class FakeConnection:
        @asynccontextmanager
        async def channel(self):
            yield FakeChannel()

    app.state.rabbitmq_connection = FakeConnection()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unconfirmed = await client.post(
            "/api/messages/publish",
            json={"routing_key": "orders.q", "payload": "{}"},
        )
        ok = await client.post(
            "/api/messages/publish",
            json={"routing_key": "orders.q", "payload": '{"n": 1}', "confirm": True},
        )
        missing = await client.post(
            "/api/messages/publish",
            json={"routing_key": "ghost.q", "payload": "{}", "confirm": True},
        )
        audit = await client.get("/api/audit?limit=5")
    await app.state.database.close()

    assert unconfirmed.status_code == 400
    assert ok.status_code == 200
    assert ok.json()["content_type"] == "application/json"
    assert published == [(b'{"n": 1}', "orders.q")]
    assert missing.status_code == 404
    events = audit.json()["events"]
    assert events[0]["action"] == "publish" and events[0]["result"] == "failed"
    assert events[1]["action"] == "publish" and events[1]["result"] == "success"
    assert events[1]["target_queue"] == "orders.q"


@pytest.mark.asyncio
async def test_topology_is_cached(tmp_path) -> None:
    """The topology view is the most expensive management read — served from a
    short TTL cache so repeated visits don't re-poll the broker."""
    app = create_app(
        Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/tc.db")
    )
    calls = {"n": 0}

    class CountingManagementClient:
        async def list_exchanges(self) -> list[dict[str, object]]:
            calls["n"] += 1
            return []

        async def list_bindings(self) -> list[dict[str, object]]:
            return []

        async def list_queues(self) -> list[dict[str, object]]:
            return []

    app.state.management_client = CountingManagementClient()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/topology")).status_code == 200
        assert (await client.get("/api/topology")).status_code == 200

    assert calls["n"] == 1  # second hit came from the cache
