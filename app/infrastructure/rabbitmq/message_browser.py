import base64
import json
import zlib
from typing import Any, cast

from aio_pika.abc import AbstractIncomingMessage

from app.domain.fingerprint import message_fingerprint
from app.domain.models import MessageRecord
from app.domain.xdeath import parse_x_death
from app.infrastructure.rabbitmq.connection import RabbitMQConnection


class MessageBrowser:
    def __init__(self, connection: RabbitMQConnection) -> None:
        self._connection = connection

    async def list_messages(self, queue_name: str, limit: int) -> list[MessageRecord]:
        messages: list[AbstractIncomingMessage] = []
        async with self._connection.channel() as channel:
            try:
                queue = await cast(Any, channel).declare_queue(queue_name, passive=True)
                for _ in range(limit):
                    message = await queue.get(no_ack=False, fail=False)
                    if message is None:
                        break
                    messages.append(message)
                records = [self._to_record(queue_name, message) for message in messages]
            finally:
                await self._requeue(messages)
        return records

    async def _requeue(self, messages: list[AbstractIncomingMessage]) -> None:
        for message in reversed(messages):
            if not message.processed:
                await message.nack(requeue=True)

    @staticmethod
    def _to_record(queue_name: str, message: AbstractIncomingMessage) -> MessageRecord:
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
        return MessageRecord(
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
