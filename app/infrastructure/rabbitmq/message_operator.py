from typing import Any, cast

from aio_pika import Message
from aio_pika.abc import AbstractIncomingMessage
from aio_pika.exceptions import DeliveryError
from aiormq.exceptions import ChannelInvalidStateError

from app.domain.models import MessageRecord, ReplayTarget
from app.infrastructure.rabbitmq.connection import RabbitMQConnection
from app.infrastructure.rabbitmq.message_browser import MessageBrowser


class MessageOperator:
    def __init__(self, connection: RabbitMQConnection) -> None:
        self._connection = connection

    async def operate(
        self,
        *,
        source_queue: str,
        fingerprint: str,
        action: str,
        target: ReplayTarget | None = None,
        replay_headers: dict[str, Any] | None = None,
        max_scan: int = 100,
    ) -> dict[str, object]:
        scanned: list[AbstractIncomingMessage] = []
        matches: list[tuple[AbstractIncomingMessage, MessageRecord]] = []
        async with self._connection.channel() as channel:
            try:
                queue = await cast(Any, channel).declare_queue(source_queue, passive=True)
                for _ in range(max_scan):
                    message = await queue.get(no_ack=False, fail=False)
                    if message is None:
                        break
                    scanned.append(message)
                    record = MessageBrowser._to_record(source_queue, message)
                    if record.fingerprint == fingerprint:
                        matches.append((message, record))

                if len(matches) != 1:
                    raise LookupError(
                        f"Message {fingerprint} was not found uniquely in {source_queue}"
                    )
                target_message, target_record = matches[0]

                if action in {"copy", "move", "park"}:
                    if target is None:
                        raise ValueError("A publish target is required")
                    await self._ensure_target(channel, target, create=action == "park")
                    await self._publish(channel, target_record, target, replay_headers or {})

                if action in {"move", "park", "delete"}:
                    await target_message.ack()
                elif action == "copy":
                    await target_message.nack(requeue=True)
                else:
                    raise ValueError(f"Unsupported message action: {action}")

                await self._requeue_other_messages(scanned, target_message)
                return {
                    "status": "success",
                    "action": action,
                    "fingerprint": fingerprint,
                    "target": _target_to_dict(target),
                }
            except Exception:
                await self._requeue_unprocessed(scanned)
                raise

    async def operate_bulk(
        self,
        *,
        source_queue: str,
        fingerprints: frozenset[str],
        action: str,
        target: ReplayTarget | None = None,
        replay_headers: dict[str, Any] | None = None,
        max_scan: int = 500,
    ) -> list[dict[str, object]]:
        """Act on every approved fingerprint independently.

        Per-message safety spine: publish before ack, DeliveryError requeues
        that message and the batch continues. Duplicated fingerprints are
        skipped and reported, never guessed. A channel-level failure aborts the
        whole batch — the broker requeues everything unacked on channel close.
        """
        if action not in {"copy", "move", "park", "delete"}:
            raise ValueError(f"Unsupported message action: {action}")
        scanned: list[AbstractIncomingMessage] = []
        async with self._connection.channel() as channel:
            try:
                queue = await cast(Any, channel).declare_queue(source_queue, passive=True)
                groups: dict[str, list[tuple[AbstractIncomingMessage, MessageRecord]]] = {}
                for _ in range(max_scan):
                    message = await queue.get(no_ack=False, fail=False)
                    if message is None:
                        break
                    scanned.append(message)
                    record = MessageBrowser._to_record(source_queue, message)
                    groups.setdefault(record.fingerprint, []).append((message, record))

                if action in {"copy", "move", "park"}:
                    if target is None:
                        raise ValueError("A publish target is required")
                    # Verified before anything is consumed so a missing target
                    # aborts the batch with every message still in the queue.
                    await self._ensure_target(channel, target, create=action == "park")

                results: list[dict[str, object]] = []
                for fingerprint in sorted(fingerprints):
                    group = groups.get(fingerprint)
                    if not group:
                        results.append({"fingerprint": fingerprint, "status": "not_found"})
                        continue
                    if len(group) > 1:
                        results.append(
                            {"fingerprint": fingerprint, "status": "skipped_duplicate"}
                        )
                        continue
                    message, record = group[0]
                    try:
                        if action in {"copy", "move", "park"}:
                            headers = {
                                **(replay_headers or {}),
                                "x-queuelens-original-fingerprint": record.fingerprint,
                            }
                            await self._publish(
                                channel, record, cast(ReplayTarget, target), headers
                            )
                        if action == "copy":
                            await message.nack(requeue=True)
                        else:
                            await message.ack()
                        results.append({"fingerprint": fingerprint, "status": "success"})
                    except DeliveryError as error:
                        await message.nack(requeue=True)
                        results.append(
                            {
                                "fingerprint": fingerprint,
                                "status": "failed",
                                "error": str(error) or "message was unroutable",
                            }
                        )
                for message in reversed(scanned):
                    if not message.processed:
                        await message.nack(requeue=True)
                return results
            except Exception:
                await self._requeue_unprocessed(scanned)
                raise

    async def _ensure_target(
        self, channel: Any, target: ReplayTarget, *, create: bool
    ) -> None:
        if target.type == "queue":
            if not target.queue:
                raise ValueError("Queue replay target requires queue")
            # Parking queues are created on demand; replay targets must already
            # exist. Either way an unroutable publish can never silently drop
            # the message.
            await cast(Any, channel).declare_queue(
                target.queue, durable=True, passive=not create
            )
            return
        if target.type == "exchange":
            if not target.exchange or target.routing_key is None:
                raise ValueError("Exchange replay target requires exchange and routing_key")
            await channel.get_exchange(target.exchange, ensure=True)
            return
        raise ValueError(f"Unsupported replay target type: {target.type}")

    async def _requeue_other_messages(
        self,
        messages: list[AbstractIncomingMessage],
        target_message: AbstractIncomingMessage,
    ) -> None:
        for message in reversed(messages):
            if message is not target_message and not message.processed:
                await message.nack(requeue=True)

    async def _requeue_unprocessed(self, messages: list[AbstractIncomingMessage]) -> None:
        for message in reversed(messages):
            if not message.processed:
                try:
                    await message.nack(requeue=True)
                except ChannelInvalidStateError:
                    # RabbitMQ requeues unacked deliveries when the channel closes.
                    return

    async def _publish(
        self,
        channel: Any,
        record: MessageRecord,
        target: ReplayTarget,
        replay_headers: dict[str, Any],
    ) -> None:
        properties = record.properties
        outgoing = Message(
            body=record.body,
            headers={**record.headers, **replay_headers},
            content_type=record.content_type,
            content_encoding=properties.get("content_encoding"),
            delivery_mode=properties.get("delivery_mode"),
            priority=properties.get("priority"),
            correlation_id=record.correlation_id,
            reply_to=properties.get("reply_to"),
            expiration=properties.get("expiration"),
            message_id=record.message_id,
            timestamp=record.timestamp,
            type=properties.get("type"),
            user_id=properties.get("user_id"),
            app_id=properties.get("app_id"),
        )
        if target.type == "queue":
            if not target.queue:
                raise ValueError("Queue replay target requires queue")
            await channel.default_exchange.publish(outgoing, routing_key=target.queue)
            return
        if target.type == "exchange":
            if not target.exchange or target.routing_key is None:
                raise ValueError("Exchange replay target requires exchange and routing_key")
            exchange = await channel.get_exchange(target.exchange, ensure=False)
            await exchange.publish(outgoing, routing_key=target.routing_key)
            return
        raise ValueError(f"Unsupported replay target type: {target.type}")


def _target_to_dict(target: ReplayTarget | None) -> dict[str, str | None] | None:
    if target is None:
        return None
    return {
        "type": target.type,
        "queue": target.queue,
        "exchange": target.exchange,
        "routing_key": target.routing_key,
    }
