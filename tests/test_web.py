import httpx
import pytest

from app.config import Settings
from app.domain.models import QueueInfo
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

