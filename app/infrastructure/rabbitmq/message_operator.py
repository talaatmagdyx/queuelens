import json
from typing import Any, cast

import aiormq
from aio_pika import Message
from aio_pika.abc import AbstractIncomingMessage
from aio_pika.exceptions import DeliveryError
from aiormq.exceptions import (
    ChannelAccessRefused,
    ChannelInvalidStateError,
    ChannelPreconditionFailed,
)

from app.domain.models import MessageRecord, ReplayTarget
from app.domain.xdeath import DEATHS_HEADER, deaths
from app.infrastructure.rabbitmq.connection import RabbitMQConnection
from app.infrastructure.rabbitmq.message_browser import (
    MessageBrowser,
    QueueLocks,
    fetch,
    requeue,
)


def error_text(error: BaseException, fallback: str = "") -> str:
    """str() of a broker-client exception, safely: failure paths must never fail while
    describing a failure (aiormq 7's DeliveryError raises from __str__ when it carries
    no frame)."""
    try:
        return str(error) or fallback or type(error).__name__
    except Exception:  # noqa: BLE001 - describing the error is best-effort
        return fallback or type(error).__name__


# The broker refused the operation and closed the channel; the channel's unacked messages
# go back to their queue, so for a single action nothing changed.
REFUSALS = (ChannelPreconditionFailed, ChannelAccessRefused)


def refusal_text(error: BaseException) -> str:
    text = error_text(error)
    if "user_id" in text:
        # RabbitMQ validates user_id against the publishing connection's user
        text += (
            " — the message carries another broker user's user_id, which RabbitMQ accepts "
            "only from that user or one with the impersonator tag"
        )
    return f"RabbitMQ refused it: {text}"


def _milliseconds(seconds: float | None) -> str | None:
    # aio-pika decodes the broker's millisecond string to float seconds (float(ms) / 1000);
    # round() recovers it exactly, int() — what aio-pika re-encodes with — can drop 1 ms
    return None if seconds is None else str(round(seconds * 1000))


class _Replay(Message):
    """A copy that keeps the original's expiration exactly. aio-pika (9 and 10) encodes
    expiration from float seconds with int() truncation: "1001" ms → 1.001 s → "1000"."""

    __slots__ = ("_expiration_ms",)

    def __init__(self, body: bytes, *, expiration_ms: str | None, **kwargs: Any) -> None:
        super().__init__(body, **kwargs)
        self._expiration_ms = expiration_ms

    @property
    def properties(self) -> aiormq.spec.Basic.Properties:
        properties = super().properties
        properties.expiration = self._expiration_ms
        return properties


class MessageOperator:
    def __init__(self, connection: RabbitMQConnection, locks: QueueLocks | None = None) -> None:
        self._connection = connection
        self._locks = locks or QueueLocks()

    async def operate(
        self,
        *,
        source_queue: str,
        fingerprint: str,
        action: str,
        target: ReplayTarget | None = None,
        replay_headers: dict[str, Any] | None = None,
        max_scan: int = 100,
        whole: bool = False,
    ) -> dict[str, object]:
        scanned: list[AbstractIncomingMessage] = []
        matches: list[tuple[AbstractIncomingMessage, MessageRecord]] = []
        async with self._locks(source_queue), self._connection.channel() as channel:
            try:
                queue = await cast(Any, channel).declare_queue(source_queue, passive=True)
                await fetch(queue, source_queue, max_scan, scanned, whole=whole)
                for message in scanned:
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
                    await self._publish(channel, target_record, target, replay_headers or {},
                                        replay=action != "park")

                if action in {"move", "park", "delete"}:
                    await target_message.ack()
                elif action == "copy":
                    await target_message.nack(requeue=True)
                else:
                    raise ValueError(f"Unsupported message action: {action}")

                await requeue(scanned, keep=target_message)
                return {
                    "status": "success",
                    "action": action,
                    "fingerprint": fingerprint,
                    "target": _target_to_dict(target),
                    "message_id": target_record.message_id,
                    "x_death": json.loads(json.dumps(target_record.x_death, default=str)),
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
        whole: bool = False,
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
        async with self._locks(source_queue), self._connection.channel() as channel:
            try:
                queue = await cast(Any, channel).declare_queue(source_queue, passive=True)
                groups: dict[str, list[tuple[AbstractIncomingMessage, MessageRecord]]] = {}
                await fetch(queue, source_queue, max_scan, scanned, whole=whole)
                for message in scanned:
                    record = MessageBrowser._to_record(source_queue, message)
                    groups.setdefault(record.fingerprint, []).append((message, record))

                if action in {"copy", "move", "park"}:
                    if target is None:
                        raise ValueError("A publish target is required")
                    # Verified before anything is consumed so a missing target
                    # aborts the batch with every message still in the queue.
                    await self._ensure_target(channel, target, create=action == "park")

                results: list[dict[str, object]] = []
                ordered = sorted(fingerprints)
                for position, fingerprint in enumerate(ordered):
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
                    published = False
                    try:
                        if action in {"copy", "move", "park"}:
                            headers = {
                                **(replay_headers or {}),
                                "x-queuelens-original-fingerprint": record.fingerprint,
                            }
                            await self._publish(
                                channel, record, cast(ReplayTarget, target), headers,
                                replay=action != "park",
                            )
                            published = True
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
                                "error": error_text(error, "message was unroutable"),
                            }
                        )
                    except Exception as error:  # noqa: BLE001 - reported, not swallowed
                        # The channel is gone (the broker refused a publish, or the
                        # connection dropped): nothing more can be settled on it and the
                        # broker requeues every unacked message. Stop, and report what
                        # already happened instead of raising — earlier messages in this
                        # batch did move, and the route must audit each of them.
                        results.append(_halted(fingerprint, error, published, action))
                        results.extend(
                            {"fingerprint": later, "status": "not_attempted"}
                            for later in ordered[position + 1 :]
                        )
                        break
                await self._requeue_unprocessed(scanned)
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

    async def _requeue_unprocessed(self, messages: list[AbstractIncomingMessage]) -> None:
        for message in messages:  # in read order: a quorum queue keeps that order
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
        *,
        replay: bool,
    ) -> None:
        properties = record.properties
        headers = {**record.headers, **replay_headers}
        if replay:  # RabbitMQ 4.x restarts x-death for a republished message
            headers[DEATHS_HEADER] = deaths(record.x_death, record.headers)
        outgoing = _Replay(
            record.body,
            expiration_ms=_milliseconds(properties.get("expiration")),
            headers=headers,
            content_type=record.content_type,
            content_encoding=properties.get("content_encoding"),
            delivery_mode=properties.get("delivery_mode"),
            priority=properties.get("priority"),
            correlation_id=record.correlation_id,
            reply_to=properties.get("reply_to"),
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


def _halted(
    fingerprint: str, error: BaseException, published: bool, action: str
) -> dict[str, object]:
    if published and action == "copy":
        # the copy is confirmed and the original goes back on channel close — as intended
        return {"fingerprint": fingerprint, "status": "success"}
    if published:
        return {
            "fingerprint": fingerprint,
            "status": "failed",
            "error": f"copied to the target, but the original could not be acknowledged "
            f"({error_text(error)}) — it is back in the queue, so replaying it again "
            "would duplicate it",
        }
    text = refusal_text(error) if isinstance(error, REFUSALS) else error_text(error)
    return {"fingerprint": fingerprint, "status": "failed", "error": text}


def _target_to_dict(target: ReplayTarget | None) -> dict[str, str | None] | None:
    if target is None:
        return None
    return {
        "type": target.type,
        "queue": target.queue,
        "exchange": target.exchange,
        "routing_key": target.routing_key,
    }
