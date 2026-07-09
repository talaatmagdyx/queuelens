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
