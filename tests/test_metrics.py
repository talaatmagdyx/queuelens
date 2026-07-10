import httpx
import pytest

from app.config import Settings
from app.domain.models import QueueInfo
from app.main import create_app


@pytest.mark.asyncio
async def test_metrics_exposes_ready_and_dlq_gauges(tmp_path) -> None:
    app = create_app(
        Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db")
    )
    await app.state.database.start()

    class FakeQueueService:
        async def list_queues(self, dlq_only: bool = False) -> list[QueueInfo]:
            return [
                QueueInfo(
                    name="orders.dlq",
                    vhost="/",
                    messages=7,
                    messages_ready=7,
                    messages_unacked=0,
                    consumers=0,
                    durable=True,
                    is_dlq=True,
                )
            ]

    class FakeActionService:
        async def delete(self, **_kwargs: object) -> dict[str, object]:
            return {"status": "success", "action": "delete", "fingerprint": "x", "target": None}

    app.state.queue_service = FakeQueueService()
    app.state.action_service = FakeActionService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/messages/delete",
            json={"source_queue": "orders.dlq", "fingerprint": "a" * 64, "confirm": True},
        )
        response = await client.get("/metrics")
    await app.state.database.close()

    assert response.status_code == 200
    body = response.text
    # broker is not connected in tests -> ready gauge reports 0
    assert "queuelens_rabbitmq_ready 0.0" in body
    assert 'queuelens_dlq_messages{queue="orders.dlq"} 7.0' in body
    assert 'queuelens_actions_total{action="delete",result="success"}' in body
    assert 'queuelens_operation_duration_seconds_bucket{action="delete"' in body
