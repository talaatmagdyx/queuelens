from collections.abc import Sequence
from typing import Any

from app.domain.models import QueueInfo
from app.infrastructure.rabbitmq.management_client import RabbitMQManagementClient


class QueueService:
    def __init__(self, management: RabbitMQManagementClient) -> None:
        self._management = management

    async def list_queues(self, dlq_only: bool = False) -> list[QueueInfo]:
        raw_queues = await self._management.list_queues()
        queues = [self._to_queue_info(item) for item in raw_queues]
        if dlq_only:
            queues = [queue for queue in queues if queue.is_dlq]
        return queues

    async def get_queue(self, queue_name: str) -> QueueInfo:
        return self._to_queue_info(await self._management.get_queue(queue_name))

    @staticmethod
    def _to_queue_info(raw: dict[str, Any]) -> QueueInfo:
        arguments = raw.get("arguments") or {}
        return QueueInfo(
            name=str(raw.get("name", "")),
            vhost=str(raw.get("vhost", "/")),
            messages=int(raw.get("messages", 0)),
            messages_ready=int(raw.get("messages_ready", 0)),
            messages_unacked=int(raw.get("messages_unacknowledged", 0)),
            consumers=int(raw.get("consumers", 0)),
            durable=bool(raw.get("durable", False)),
            arguments=arguments,
            is_dlq=QueueService._looks_like_dlq(str(raw.get("name", "")), arguments),
        )

    @staticmethod
    def _looks_like_dlq(name: str, arguments: dict[str, Any]) -> bool:
        normalized_name = name.lower()
        name_match = any(token in normalized_name for token in (".dlq", "_dlq", "dead"))
        argument_match = any(
            "dead-letter" in str(key).lower() or "dead-letter" in str(value).lower()
            for key, value in arguments.items()
        )
        return name_match or argument_match


def queues_to_dicts(queues: Sequence[QueueInfo]) -> list[dict[str, Any]]:
    return [
        {
            "name": queue.name,
            "vhost": queue.vhost,
            "messages": queue.messages,
            "messages_ready": queue.messages_ready,
            "messages_unacked": queue.messages_unacked,
            "consumers": queue.consumers,
            "durable": queue.durable,
            "arguments": queue.arguments,
            "is_dlq": queue.is_dlq,
        }
        for queue in queues
    ]

