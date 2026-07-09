import aio_pika
import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.fixture
def application():
    return create_app()


@pytest.mark.asyncio
async def test_health_and_readiness(application) -> None:
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/health")
        not_ready_response = await client.get("/ready")
        application.state.ready = True
        ready_response = await client.get("/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert not_ready_response.status_code == 503
    assert not_ready_response.json() == {"status": "not_ready"}
    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_lifespan_stays_not_ready_when_rabbitmq_is_unavailable(monkeypatch, tmp_path) -> None:
    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("RabbitMQ unavailable")

    monkeypatch.setattr(aio_pika, "connect_robust", unavailable)
    application = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/health.db",
            rabbitmq_operation_timeout_seconds=0.01,
        )
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
