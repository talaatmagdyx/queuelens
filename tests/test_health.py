import httpx
import pytest

from app.main import create_app


@pytest.fixture
def application():
    return create_app()


@pytest.mark.asyncio
async def test_health_and_readiness(application) -> None:
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/health")
        ready_response = await client.get("/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ok"}

