import base64
import dataclasses
import json
import zlib
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, cast

from aio_pika.abc import AbstractIncomingMessage

from app.domain.errors import UnsafeToBrowse
from app.domain.fingerprint import message_fingerprint
from app.domain.models import MessageRecord
from app.domain.xdeath import parse_x_death
from app.infrastructure.persistence.coordination import Coordinator
from app.infrastructure.rabbitmq.connection import RabbitMQConnection


class QueueLocks:
    """One lock per queue. A scan holds messages unacked until it requeues them, so two
    concurrent scans each see part of the queue (and an action can miss its target).
    Keyed by the broker (its Management URL) and vhost as well, so environments that
    name one broker twice share it; on PostgreSQL the lock spans every replica."""

    def __init__(self, coordinator: Coordinator | None = None, broker: str = "",
                 vhost: str = "/") -> None:
        self._coordinator = coordinator or Coordinator()
        self._scope = (broker, vhost)

    def __call__(self, queue: str) -> AbstractAsyncContextManager[None]:
        return self._coordinator.lock("queue", *self._scope, queue)


# Bodies one scan may hold (every message read stays unacked until the scan requeues it).
# ponytail: a constant — a Limits setting if operators need snapshots bigger than this.
SCAN_BYTES_BUDGET = 64 * 1024 * 1024

# Payload of a slim record: decoded on demand (with_payload) instead of kept in memory.
UNDECODED: Any = object()


def too_deep(queue_name: str, messages: int, depth: int) -> str:
    return (
        f"{queue_name} is a quorum queue holding {messages} messages, more than the browse "
        f"depth ({depth}). A quorum queue puts every returned message at the back, so "
        "browsing part of one would reorder it — QueueLens browses quorum queues only "
        "whole. Raise the browse depth (Configuration → Limits) or shovel the queue to a "
        "classic one to browse it."
    )


async def fetch(
    queue: Any,
    queue_name: str,
    limit: int,
    held: list[AbstractIncomingMessage],
    *,
    whole: bool = False,
    max_bytes: int | None = None,
) -> tuple[int, str | None]:
    """basic.get up to `limit` messages into `held` — the caller's list, so whatever was
    taken is requeued even when this raises. Returns (messages ready when it started, why
    it stopped early: "depth" / "memory", or None at the end of the queue).

    `whole` is for quorum queues: they return messages to the back (measured on 3.13 and
    4.1), so only a scan that reads everything and requeues it in order leaves them as
    they were. It refuses before taking anything when the queue is deeper than `limit`."""
    declared = getattr(queue, "declaration_result", None)  # Declare-Ok of the passive declare
    ready = int(getattr(declared, "message_count", 0) or 0)
    if whole and ready > limit:
        raise UnsafeToBrowse(too_deep(queue_name, ready, limit))
    size = 0
    while True:
        message = await queue.get(no_ack=False, fail=False)
        if message is None:
            return ready, None
        held.append(message)
        if len(held) > limit:  # only a whole scan gets here: the queue grew while we read
            raise UnsafeToBrowse(too_deep(queue_name, len(held), limit))
        size += len(message.body)
        if not whole and len(held) == limit:
            return ready, "depth"
        if not whole and max_bytes is not None and size >= max_bytes:
            return ready, "memory"


async def requeue(messages: list[AbstractIncomingMessage], keep: object = None) -> None:
    """Return every unsettled message in the order it was read — on a quorum queue that
    order is what the queue ends up with (classic queues restore positions regardless)."""
    for message in messages:
        if message is not keep and not message.processed:
            await message.nack(requeue=True)


@dataclass(frozen=True, slots=True)
class Scan:
    records: list[MessageRecord]
    ready: int  # messages ready when the scan started
    stopped: str | None  # "depth" / "memory" when it didn't reach the end of the queue


class MessageBrowser:
    def __init__(self, connection: RabbitMQConnection, locks: QueueLocks | None = None) -> None:
        self._connection = connection
        self._locks = locks or QueueLocks()

    async def list_messages(
        self, queue_name: str, limit: int, *, whole: bool = False
    ) -> list[MessageRecord]:
        return (await self.scan(queue_name, limit, whole=whole)).records

    async def scan(
        self,
        queue_name: str,
        limit: int,
        *,
        whole: bool = False,
        max_bytes: int | None = None,
        slim: bool = False,
    ) -> Scan:
        messages: list[AbstractIncomingMessage] = []
        async with self._locks(queue_name), self._connection.channel() as channel:
            try:
                queue = await cast(Any, channel).declare_queue(queue_name, passive=True)
                ready, stopped = await fetch(
                    queue, queue_name, limit, messages, whole=whole, max_bytes=max_bytes
                )
                records = [self._to_record(queue_name, m, slim=slim) for m in messages]
            finally:
                await requeue(messages)
        return Scan(records, ready, stopped)

    @staticmethod
    def _to_record(
        queue_name: str, message: AbstractIncomingMessage, *, slim: bool = False
    ) -> MessageRecord:
        headers = dict(message.headers or {})
        timestamp = message.timestamp
        body = bytes(message.body)
        payload, payload_format, decoded_from = _decode_payload(body, message.content_encoding)
        fingerprint = message_fingerprint(
            queue=queue_name,
            body=body,
            headers=headers,
            message_id=message.message_id,
            timestamp=timestamp,
            exchange=message.exchange or "",
            routing_key=message.routing_key or "",
        )
        properties = {
            "content_type": message.content_type,
            "content_encoding": message.content_encoding,
            "delivery_mode": message.delivery_mode,
            "priority": message.priority,
            "correlation_id": message.correlation_id,
            "reply_to": message.reply_to,
            "expiration": message.expiration,
            "message_id": message.message_id,
            "timestamp": timestamp.isoformat() if timestamp else None,
            "type": message.type,
            "user_id": message.user_id,
            "app_id": message.app_id,
        }
        record = MessageRecord(
            fingerprint=fingerprint,
            source_queue=queue_name,
            body=body,
            payload=payload,
            payload_format=payload_format,
            payload_size=len(body),
            content_type=message.content_type,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            timestamp=timestamp,
            exchange=message.exchange or "",
            routing_key=message.routing_key or "",
            headers=headers,
            properties=properties,
            redelivered=bool(message.redelivered),
            x_death=parse_x_death(headers),
            decoded_from=decoded_from,
            payload_encoded=base64.b64encode(body).decode("ascii") if decoded_from else None,
        )
        # a snapshot keeps raw bodies only; decoded payloads (Python objects can be many
        # times the body) are rebuilt per page by with_payload()
        if slim:
            return dataclasses.replace(record, payload=UNDECODED, payload_encoded=None)
        return record


def with_payload(record: MessageRecord) -> MessageRecord:
    """A slim record (snapshot) with its payload decoded again, for display."""
    if record.payload is not UNDECODED:
        return record
    payload, _format, decoded_from = _decode_payload(
        record.body, record.properties.get("content_encoding")
    )
    encoded = base64.b64encode(record.body).decode("ascii") if decoded_from else None
    return dataclasses.replace(record, payload=payload, payload_encoded=encoded)


# Cap decompression output so a hostile message can't balloon memory (zip bomb).
MAX_DECODED_BYTES = 4 * 1024 * 1024


def _decompress(body: bytes, encoding: str) -> bytes | None:
    """gzip / zlib / raw-deflate, size-capped; None when it doesn't inflate cleanly."""
    # 32+MAX_WBITS auto-detects gzip and zlib headers; "deflate" in the wild is
    # sometimes raw deflate (no header), so fall back to -MAX_WBITS for it.
    tries = [32 + zlib.MAX_WBITS] + ([-zlib.MAX_WBITS] if encoding == "deflate" else [])
    for wbits in tries:
        try:
            inflater = zlib.decompressobj(wbits)
            out = inflater.decompress(body, MAX_DECODED_BYTES)
            if inflater.unconsumed_tail:  # would exceed the cap — leave it encoded
                return None
            return out + inflater.flush()
        except zlib.error:
            continue
    return None


def _decode_payload(body: bytes, content_encoding: str | None) -> tuple[object, str, str | None]:
    """Render the payload, transparently inflating compressed bodies.

    Returns (payload, format, decoded_from) — decoded_from names the compression
    that was undone ("gzip"/"deflate") or is None when the body was used as-is."""
    encoding = (content_encoding or "").strip().lower()
    decoded_from = None
    if encoding in ("gzip", "x-gzip", "deflate"):
        inflated = _decompress(body, encoding)
        if inflated is not None:
            body = inflated
            decoded_from = "gzip" if "gzip" in encoding else "deflate"
    try:
        return json.loads(body.decode("utf-8")), "json", decoded_from
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            return body.decode("utf-8"), "text", decoded_from
        except UnicodeDecodeError:
            return base64.b64encode(body).decode("ascii"), "base64", decoded_from
