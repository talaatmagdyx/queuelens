import httpx
import pytest

from app.application.queue_service import QueueService
from app.config import Settings
from app.infrastructure.rabbitmq.management_client import RabbitMQManagementClient


def settings() -> Settings:
    return Settings(
        rabbitmq_management_url="http://management.test",
        rabbitmq_management_username="user",
        rabbitmq_management_password="password",
        rabbitmq_vhost="/",
    )


@pytest.mark.asyncio
async def test_management_client_lists_queues_with_encoded_vhost() -> None:
    requested_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                {
                    "name": "orders.dlq",
                    "vhost": "/",
                    "messages": 3,
                    "messages_ready": 2,
                    "messages_unacknowledged": 1,
                    "consumers": 0,
                    "durable": True,
                    "arguments": {},
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://management.test")
    management = RabbitMQManagementClient(settings(), client=client)
    queues = await QueueService(management).list_queues(dlq_only=True)
    await client.aclose()

    assert requested_paths == ["http://management.test/api/queues/%2F"]
    assert len(queues) == 1
    assert queues[0].name == "orders.dlq"
    assert queues[0].is_dlq is True



@pytest.mark.asyncio
async def test_source_queues_with_dlx_arguments_are_not_listed_as_dlqs() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        def queue(name: str, arguments: dict) -> dict:
            return {
                "name": name,
                "vhost": "/",
                "messages": 0,
                "messages_ready": 0,
                "messages_unacknowledged": 0,
                "consumers": 0,
                "durable": True,
                "arguments": arguments,
            }

        return httpx.Response(
            200,
            json=[
                # source queue declaring a DLX -> NOT a DLQ
                queue(
                    "orders.processing",
                    {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": "orders.failed"},
                ),
                # referenced as a dead-letter target -> IS a DLQ despite the name
                queue("orders.failed", {}),
                # matches by name convention
                queue("email.dlq", {}),
                # unrelated queue
                queue("email.delivery", {}),
            ],
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://management.test"
    )
    management = RabbitMQManagementClient(settings(), client=client)
    queues = await QueueService(management).list_queues(dlq_only=True)
    await client.aclose()

    assert sorted(queue.name for queue in queues) == ["email.dlq", "orders.failed"]
