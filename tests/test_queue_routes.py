import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_queue_route_returns_only_dlq_queues() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://management.test/api/queues/%2F"
        return httpx.Response(
            200,
            json=[
                {
                    "name": "orders.dlq",
                    "vhost": "/",
                    "messages": 1,
                    "messages_ready": 1,
                    "messages_unacknowledged": 0,
                    "consumers": 0,
                    "durable": True,
                    "arguments": {},
                },
                {
                    "name": "orders",
                    "vhost": "/",
                    "messages": 0,
                    "messages_ready": 0,
                    "messages_unacknowledged": 0,
                    "consumers": 1,
                    "durable": True,
                    "arguments": {},
                },
            ],
        )

    app = create_app(Settings(auth_enabled=False))
    management = app.state.management_client
    management._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://management.test"
    )
    app.state.ready = True

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/queues", params={"dlq_only": "true"})

    await management.close()
    assert response.status_code == 200
    assert response.json()["queues"][0]["name"] == "orders.dlq"
    assert len(response.json()["queues"]) == 1
