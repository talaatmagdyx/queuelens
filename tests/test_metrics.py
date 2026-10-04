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
    assert ('queuelens_dlq_messages{environment="development",queue="orders.dlq",vhost="/"} 7.0'
            in body)
    assert 'queuelens_actions_total{action="delete",result="success"}' in body
    assert 'queuelens_operation_duration_seconds_bucket{action="delete"' in body


@pytest.mark.asyncio
async def test_metrics_summary_and_alert_rules(tmp_path) -> None:
    app = create_app(
        Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db")
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

    app.state.queue_service = FakeQueueService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        summary = await client.get("/api/metrics/summary")
        rules = await client.get("/api/metrics/alert-rules")
    await app.state.database.close()

    assert summary.status_code == 200
    body = summary.json()
    assert body["rabbitmq_ready"] is False  # no broker in tests
    assert {"queue": "orders.dlq", "messages": 7} in body["dlq"]
    assert body["dlq_backlog"] >= 7
    # delete action from the prior test's counter (shared registry) or zero — shape only
    assert isinstance(body["actions"], list)
    assert isinstance(body["operations"], list)

    assert rules.status_code == 200
    assert "QueueLensBrokerDown" in rules.text
    assert "queuelens_dlq_messages" in rules.text


@pytest.mark.asyncio
async def test_metrics_cover_every_environment_and_an_unreachable_one_is_down(tmp_path) -> None:
    """Every environment and vhost is scraped; one that can't be read is reported down,
    not as having no DLQs, and doesn't hold up the others."""
    app = create_app(Settings(
        auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/e.db",
        environments_json='{"staging": {"vhosts": ["ql-staging"]}, '
                          '"gone": {"management_url": "http://127.0.0.1:9"}}',
    ))
    await app.state.database.start()

    class Dlqs:
        def __init__(self, name: str, messages: int) -> None:
            self.queue = QueueInfo(name=name, vhost="/", messages=messages, messages_ready=messages,
                                   messages_unacked=0, consumers=0, durable=True, is_dlq=True)

        async def list_queues(self, dlq_only: bool = False) -> list[QueueInfo]:
            return [self.queue]

    app.state.queue_service = Dlqs("orders.dlq", 7)
    staging = await app.state.environment_manager.queue_service_for("staging", "ql-staging")
    staging.list_queues = Dlqs("orders.dlq", 2).list_queues  # same name, other vhost
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/metrics")).text
    await app.state.database.close()

    dlq = 'queuelens_dlq_messages{environment="%s",queue="orders.dlq",vhost="%s"} %s'
    assert dlq % ("development", "/", "7.0") in body
    assert dlq % ("staging", "ql-staging", "2.0") in body
    assert 'queuelens_management_up{environment="staging",vhost="ql-staging"} 1.0' in body
    assert 'queuelens_management_up{environment="gone",vhost="/"} 0.0' in body
    assert 'environment="gone",queue=' not in body
