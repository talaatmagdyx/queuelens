import httpx
import pytest

from app.config import Settings
from app.domain.models import MessageRecord, QueueInfo
from app.main import create_app


@pytest.mark.asyncio
async def test_login_and_dashboard_render_html() -> None:
    app = create_app(Settings(auth_enabled=False))

    class FakeQueueService:
        async def list_queues(self, dlq_only: bool = False) -> list[QueueInfo]:
            assert dlq_only is True
            return [
                QueueInfo(
                    name="orders.dlq",
                    vhost="/",
                    messages=2,
                    messages_ready=2,
                    messages_unacked=0,
                    consumers=0,
                    durable=True,
                    is_dlq=True,
                )
            ]

    app.state.queue_service = FakeQueueService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.get("/login")
        dashboard = await client.get("/")

    assert login.status_code == 200
    assert "QueueLens" in login.text
    assert dashboard.status_code == 200
    assert "orders.dlq" in dashboard.text


@pytest.mark.asyncio
async def test_message_detail_renders_action_controls() -> None:
    app = create_app(Settings(auth_enabled=False))
    message = MessageRecord(
        fingerprint="a" * 64,
        source_queue="orders.dlq",
        body=b"{}",
        payload={},
        payload_format="json",
        payload_size=2,
        content_type="application/json",
        message_id="message-1",
        correlation_id=None,
        timestamp=None,
        exchange="orders",
        routing_key="created",
        headers={},
        properties={},
        redelivered=False,
    )

    class FakeMessageService:
        async def get_message(self, _queue: str, _fingerprint: str, _limit: int) -> MessageRecord:
            return message

    app.state.message_service = FakeMessageService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/messages/orders.dlq/{message.fingerprint}")

    assert response.status_code == 200
    assert "Replay copy" in response.text
    assert "Replay move" in response.text
    assert "runAction" in response.text


@pytest.mark.asyncio
async def test_message_detail_renders_real_xdeath_with_datetime() -> None:
    from datetime import UTC, datetime

    app = create_app(Settings(auth_enabled=False))
    died_at = datetime(2026, 7, 9, 23, 14, 39, tzinfo=UTC)
    message = MessageRecord(
        fingerprint="b" * 64,
        source_queue="real.dlq",
        body=b"{}",
        payload={},
        payload_format="json",
        payload_size=2,
        content_type="application/json",
        message_id="job-1",
        correlation_id=None,
        timestamp=None,
        exchange="",
        routing_key="real.dlq",
        headers={"x-death": [{"count": 1, "time": died_at}], "x-first-death-queue": "work.q"},
        properties={},
        redelivered=False,
        x_death=[{"count": 1, "time": died_at, "queue": "work.q", "reason": "expired"}],
    )

    class FakeMessageService:
        async def get_message(self, _queue: str, _fingerprint: str, _limit: int) -> MessageRecord:
            return message

    app.state.message_service = FakeMessageService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/messages/real.dlq/{message.fingerprint}")

    assert response.status_code == 200
    assert "2026-07-09T23:14:39+00:00" in response.text


@pytest.mark.asyncio
async def test_ambiguous_message_detail_returns_friendly_404_page() -> None:
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
        response = await client.get(f"/messages/orders.dlq/{'c' * 64}")

    assert response.status_code == 404
    assert "not found uniquely" in response.text
    assert "<html" in response.text


@pytest.mark.asyncio
async def test_unknown_queue_page_returns_404_not_500() -> None:
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
        page = await client.get("/queues/does.not.exist")
        api = await client.get("/api/queues/does.not.exist")

    assert page.status_code == 404
    assert "Queue not found" in page.text
    assert api.status_code == 404
    assert api.json() == {"detail": "Queue not found"}


@pytest.mark.asyncio
async def test_queue_page_warns_when_preview_is_capped() -> None:
    app = create_app(Settings(auth_enabled=False, max_preview_messages=2))
    message = MessageRecord(
        fingerprint="d" * 64,
        source_queue="orders.dlq",
        body=b"{}",
        payload={},
        payload_format="json",
        payload_size=2,
        content_type="application/json",
        message_id="m-1",
        correlation_id=None,
        timestamp=None,
        exchange="",
        routing_key="orders.dlq",
        headers={},
        properties={},
        redelivered=False,
    )

    class FakeQueueService:
        async def get_queue(self, name: str) -> QueueInfo:
            return QueueInfo(
                name=name,
                vhost="/",
                messages=4812,
                messages_ready=4812,
                messages_unacked=0,
                consumers=0,
                durable=True,
                is_dlq=True,
            )

    class FakeMessageService:
        async def list_messages(self, _queue: str, limit: int) -> list[MessageRecord]:
            return [message] * limit

    app.state.queue_service = FakeQueueService()
    app.state.message_service = FakeMessageService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/queues/orders.dlq")

    assert response.status_code == 200
    assert "Showing 2 of 4812 messages" in response.text


@pytest.mark.asyncio
async def test_queues_index_lists_all_queues_with_kind_and_status() -> None:
    app = create_app(Settings(auth_enabled=False))

    class FakeQueueService:
        async def list_queues(self, dlq_only: bool = False) -> list[QueueInfo]:
            assert dlq_only is False
            def q(name: str, messages: int, consumers: int, is_dlq: bool, kind: str) -> QueueInfo:
                return QueueInfo(
                    name=name, vhost="/", messages=messages, messages_ready=messages,
                    messages_unacked=0, consumers=consumers, durable=True,
                    is_dlq=is_dlq, kind=kind,
                )
            return [
                q("orders.dlq", 121, 0, True, "dlq"),
                q("orders.dlq.parking", 4, 0, True, "parking"),
                q("orders.created", 0, 3, False, "normal"),
            ]

    app.state.queue_service = FakeQueueService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/queues")

    assert response.status_code == 200
    assert "orders.dlq.parking" in response.text
    assert "orders.created" in response.text
    assert 'k-parking' in response.text
    assert 'k-normal' in response.text
    assert 's-active' in response.text  # consumer-backed normal queue


@pytest.mark.asyncio
async def test_multi_user_auth_and_users_page(tmp_path) -> None:
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
        anonymous = await client.get("/users")
        wrong = await client.get("/users", auth=("sre", "bad"))
        sre = await client.get("/users", auth=("sre", "sre-pw"))
        admin = await client.get("/users", auth=("admin", "root-pw"))
    await app.state.database.close()

    assert anonymous.status_code == 401
    assert wrong.status_code == 401
    assert sre.status_code == 200
    assert admin.status_code == 200
    assert "Administrator" in admin.text
    assert "sre" in admin.text


@pytest.mark.asyncio
async def test_replay_wizard_renders_with_message_context() -> None:
    app = create_app(Settings(auth_enabled=False))
    message = MessageRecord(
        fingerprint="e" * 64,
        source_queue="orders.dlq",
        body=b"{}",
        payload={"order": 1},
        payload_format="json",
        payload_size=12,
        content_type="application/json",
        message_id="m-9",
        correlation_id=None,
        timestamp=None,
        exchange="",
        routing_key="orders.dlq",
        headers={},
        properties={},
        redelivered=False,
        x_death=[{"count": 2, "reason": "rejected", "queue": "orders"}],
    )

    class FakeMessageService:
        async def get_message(self, _queue: str, _fp: str, _limit: int) -> MessageRecord:
            return message

    app.state.message_service = FakeMessageService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        replay = await client.get(f"/messages/orders.dlq/{message.fingerprint}/replay")
        park = await client.get(f"/messages/orders.dlq/{message.fingerprint}/park")

    assert replay.status_code == 200
    assert "Replay (Move)" in replay.text
    assert "Type the source queue name to confirm" in replay.text
    assert park.status_code == 200
    assert "Parking Queue" in park.text
    assert "orders.dlq.parking" in park.text
