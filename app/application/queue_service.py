from collections.abc import Sequence
from typing import Any

from app.domain.models import QueueInfo
from app.infrastructure.rabbitmq.management_client import (
    RabbitMQManagementClient,
    RabbitMQManagementError,
)


class UnsafeToBrowse(RuntimeError):
    """Browsing this queue would count as deliveries and could drop messages."""


def _major(rabbitmq_version: str | None) -> int:
    try:
        return int(str(rabbitmq_version or "0").split(".")[0])
    except ValueError:
        return 0  # unknown → treated like 3.x, the stricter reading


def delivery_limit(raw: dict[str, Any], rabbitmq_version: str | None) -> int | None:
    """Effective delivery limit of a quorum queue, or None when it has none.

    AMQP 0-9-1 has no browse: a preview is basic.get + requeue, and quorum queues
    count every return as a delivery (AMQP 1.0 `released` too, on 4.x) — past the
    limit the message is dropped or dead-lettered away. Classic queues don't count.

    Semantics measured against real brokers (3.13.7, 4.1.8):
    - 4.x: the lowest non-negative value of the queue argument and the policy wins;
      -1 means "no limit from this source"; nothing configured means the default, 20.
    - 3.x: no default; every configured value is a limit — -1 is *not* unlimited,
      it drops a message on its first return."""
    arguments = raw.get("arguments") or {}
    if (raw.get("type") or arguments.get("x-queue-type")) != "quorum":
        return None
    policy = raw.get("effective_policy_definition")
    policy = policy if isinstance(policy, dict) else {}
    values = [int(v) for v in (arguments.get("x-delivery-limit"), policy.get("delivery-limit"))
              if v is not None]
    if _major(rabbitmq_version) < 4:
        return max(0, min(values)) if values else None
    limits = [v for v in values if v >= 0]
    if limits:
        return min(limits)
    return None if values else 20


class QueueService:
    def __init__(self, management: RabbitMQManagementClient) -> None:
        self._management = management
        self._rabbitmq_version: str | None = None

    async def assert_browsable(self, queue_name: str) -> None:
        """Refuse previews, scans and actions on queues where requeue is destructive."""
        try:
            raw = await self._management.get_queue(queue_name)
        except RabbitMQManagementError as error:
            if error.status_code == 404:
                return  # the AMQP path reports the missing queue as usual
            raise
        if self._rabbitmq_version is None:
            overview = await self._management.overview()
            self._rabbitmq_version = str(overview.get("rabbitmq_version"))
        quorum = (raw.get("type") or (raw.get("arguments") or {}).get("x-queue-type")) == "quorum"
        if quorum and "messages" not in raw:
            # the first stats emission (seconds after declaration) also carries the
            # applied policy — until then a policy-defined limit is invisible: fail closed
            raise UnsafeToBrowse(
                f"{queue_name} is a quorum queue whose statistics aren't available yet, so its "
                "delivery limit can't be checked — try again in a few seconds."
            )
        limit = delivery_limit(raw, self._rabbitmq_version)
        if limit is not None:
            fix = (
                "set the limit to -1 on dead-letter queues (policy delivery-limit or "
                "x-delivery-limit)"
                if _major(self._rabbitmq_version) >= 4
                else "remove x-delivery-limit / the delivery-limit policy from dead-letter "
                "queues (on RabbitMQ 3.x, -1 is not unlimited — it drops on the first return)"
            )
            raise UnsafeToBrowse(
                f"{queue_name} is a quorum queue with a delivery limit of {limit}: every preview "
                "or scan counts as a delivery and would eventually drop messages, so QueueLens "
                f"will not browse or act on it. To browse it, {fix}."
            )

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
        is_dlq = QueueService._looks_like_dlq(name, dead_letter_targets)
        return QueueInfo(
            name=name,
            vhost=str(raw.get("vhost", "/")),
            messages=int(raw.get("messages", 0)),
            messages_ready=int(raw.get("messages_ready", 0)),
            messages_unacked=int(raw.get("messages_unacknowledged", 0)),
            consumers=int(raw.get("consumers", 0)),
            durable=bool(raw.get("durable", False)),
            arguments=arguments,
            is_dlq=is_dlq,
            kind=QueueService._classify(name) if is_dlq else "normal",
            queue_type=str(
                raw.get("type") or arguments.get("x-queue-type") or "classic"
            ),
            publish_rate=(raw.get("message_stats") or {})
            .get("publish_details", {})
            .get("rate"),
            idle_since=raw.get("idle_since"),
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


def _status(queue: QueueInfo) -> str:
    """One operator-facing word per queue. DLQ-family queues get severity;
    normal queues are active (consuming) or idle."""
    if queue.kind == "parking":
        return "parking"
    if queue.is_dlq:
        return _severity(queue.messages)
    return "active" if queue.consumers > 0 else "idle"


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
            "queue_type": queue.queue_type,
            "severity": _severity(queue.messages),
            "status": _status(queue),
            "publish_rate": queue.publish_rate,
            "idle_since": queue.idle_since,
        }
        for queue in queues
    ]
