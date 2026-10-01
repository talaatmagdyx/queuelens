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


@pytest.mark.asyncio
async def test_queue_type_extracted_from_type_field_or_arguments() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        def queue(name: str, extra: dict) -> dict:
            return {
                "name": name, "vhost": "/", "messages": 0, "messages_ready": 0,
                "messages_unacknowledged": 0, "consumers": 0, "durable": True,
                "arguments": {}, **extra,
            }

        return httpx.Response(200, json=[
            queue("orders.dlq", {"type": "quorum"}),
            queue("audit.stream.dlq", {"arguments": {"x-queue-type": "stream"}}),
            queue("legacy.dlq", {}),
        ])

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://management.test"
    )
    management = RabbitMQManagementClient(settings(), client=client)
    queues = await QueueService(management).list_queues()
    await client.aclose()

    by_name = {q.name: q.queue_type for q in queues}
    assert by_name["orders.dlq"] == "quorum"
    assert by_name["audit.stream.dlq"] == "stream"
    assert by_name["legacy.dlq"] == "classic"


# Every case below was measured against real brokers (RabbitMQ 3.13.7 and 4.1.8).
@pytest.mark.parametrize(
    ("raw", "version", "expected"),
    [
        ({"type": "classic", "arguments": {"x-delivery-limit": 2}}, "4.1.8", None),
        ({"type": "quorum", "arguments": {}}, "3.13.7", None),  # 3.x: no default
        ({"type": "quorum", "arguments": {}}, "4.1.8", 20),  # 4.x default, invisible in mgmt
        ({"type": "quorum", "arguments": {"x-delivery-limit": 3}}, "3.13.7", 3),
        ({"type": "quorum", "effective_policy_definition": {"delivery-limit": 5}}, "3.13.7", 5),
        ({"type": "quorum", "arguments": {"x-delivery-limit": 9},
          "effective_policy_definition": {"delivery-limit": 4}}, "3.13.7", 4),
        # -1: unlimited on 4.x, but on 3.x it drops a message on its first return
        ({"type": "quorum", "arguments": {"x-delivery-limit": -1}}, "4.1.8", None),
        ({"type": "quorum", "effective_policy_definition": {"delivery-limit": -1}}, "4.1.8", None),
        ({"type": "quorum", "arguments": {"x-delivery-limit": -1}}, "3.13.7", 0),
        # 4.x: the lowest non-negative value wins; -1 doesn't cancel the other source
        ({"type": "quorum", "arguments": {"x-delivery-limit": 3},
          "effective_policy_definition": {"delivery-limit": -1}}, "4.1.8", 3),
        ({"type": "quorum", "arguments": {"x-delivery-limit": -1},
          "effective_policy_definition": {"delivery-limit": 3}}, "4.1.8", 3),
        ({"type": "quorum", "arguments": {"x-delivery-limit": 5},
          "effective_policy_definition": {"delivery-limit": 3}}, "4.1.8", 3),
        ({"arguments": {"x-queue-type": "quorum", "x-delivery-limit": 2},
          "effective_policy_definition": []}, None, 2),
    ],
)
def test_delivery_limit_detection(raw, version, expected) -> None:
    from app.application.queue_service import delivery_limit

    assert delivery_limit(raw, version) == expected


@pytest.mark.asyncio
async def test_assert_browsable_refuses_quorum_queues_with_a_delivery_limit() -> None:
    from app.application.queue_service import UnsafeToBrowse

    queues = {
        "safe.dlq": {"name": "safe.dlq", "type": "classic", "arguments": {}, "messages": 1},
        "quorum.dlq": {"name": "quorum.dlq", "type": "quorum", "messages": 1,
                       "arguments": {"x-delivery-limit": 2}},
        # just declared: no stats yet, so a policy-defined limit would be invisible
        "fresh.dlq": {"name": "fresh.dlq", "type": "quorum", "arguments": {}},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/overview":
            return httpx.Response(200, json={"rabbitmq_version": "3.13.7"})
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=queues[name]) if name in queues else httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://management.test")
    service = QueueService(RabbitMQManagementClient(settings(), client=client))
    await service.assert_browsable("safe.dlq")
    await service.assert_browsable("missing.dlq")  # 404 → the AMQP path reports it as usual
    with pytest.raises(UnsafeToBrowse, match="delivery limit of 2"):
        await service.assert_browsable("quorum.dlq")
    with pytest.raises(UnsafeToBrowse, match="statistics aren't available yet"):
        await service.assert_browsable("fresh.dlq")
    await client.aclose()
