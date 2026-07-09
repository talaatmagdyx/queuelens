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
