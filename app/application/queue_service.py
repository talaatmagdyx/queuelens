from collections.abc import Sequence
from typing import Any

from app.domain.models import QueueInfo
from app.infrastructure.rabbitmq.management_client import RabbitMQManagementClient


class QueueService:
    def __init__(self, management: RabbitMQManagementClient) -> None:
        self._management = management

    async def list_queues(self, dlq_only: bool = False) -> list[QueueInfo]:
        raw_queues = await self._management.list_queues()
        dead_letter_targets = self._dead_letter_targets(raw_queues)
        queues = [self._to_queue_info(item, dead_letter_targets) for item in raw_queues]
        if dlq_only:
            queues = [queue for queue in queues if queue.is_dlq]
        # riskiest first: the biggest backlog is where an operator starts
        queues.sort(key=lambda queue: queue.messages, reverse=True)
        return queues

    async def get_queue(self, queue_name: str) -> QueueInfo:
        return self._to_queue_info(await self._management.get_queue(queue_name), set())

    @staticmethod
    def _dead_letter_targets(raw_queues: list[dict[str, Any]]) -> set[str]:
        """Queue names other queues dead-letter into via the default exchange."""
        targets: set[str] = set()
        for raw in raw_queues:
            arguments = raw.get("arguments") or {}
            routing_key = arguments.get("x-dead-letter-routing-key")
            if arguments.get("x-dead-letter-exchange") == "" and routing_key:
                targets.add(str(routing_key))
        return targets

    @staticmethod
    def _to_queue_info(raw: dict[str, Any], dead_letter_targets: set[str]) -> QueueInfo:
        arguments = raw.get("arguments") or {}
        name = str(raw.get("name", ""))
        return QueueInfo(
            name=name,
            vhost=str(raw.get("vhost", "/")),
            messages=int(raw.get("messages", 0)),
            messages_ready=int(raw.get("messages_ready", 0)),
            messages_unacked=int(raw.get("messages_unacknowledged", 0)),
            consumers=int(raw.get("consumers", 0)),
            durable=bool(raw.get("durable", False)),
            arguments=arguments,
            is_dlq=QueueService._looks_like_dlq(name, dead_letter_targets),
            kind=QueueService._classify(name),
        )

    @staticmethod
    def _looks_like_dlq(name: str, dead_letter_targets: set[str]) -> bool:
        # A queue that *declares* x-dead-letter-* arguments is a source, not a
        # DLQ, so detection is by name convention or by being the queue that
        # another queue dead-letters into.
        normalized_name = name.lower()
        name_match = any(token in normalized_name for token in (".dlq", "_dlq", "dead"))
        parking = normalized_name.endswith((".parking", "_parking"))
        return name_match or parking or name in dead_letter_targets

    @staticmethod
    def _classify(name: str) -> str:
        """Operators treat these differently: a parking lot is deliberate
        storage, a retry queue is in-flight recovery, a DLQ is the incident."""
        normalized = name.lower()
        if normalized.endswith((".parking", "_parking")):
            return "parking"
        if "retry" in normalized:
            return "retry"
        return "dlq"


def _severity(messages: int) -> str:
    if messages == 0:
        return "empty"
    if messages <= 10:
        return "low"
    if messages <= 100:
        return "warning"
    return "attention"


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
            "kind": queue.kind,
            "severity": _severity(queue.messages),
        }
        for queue in queues
    ]
