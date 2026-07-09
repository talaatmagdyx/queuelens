import base64
import json
from datetime import datetime
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
        timestamp = _as_datetime(message.timestamp)
        body = bytes(message.body)
        payload, payload_format = _decode_payload(body)
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
        )


def _decode_payload(body: bytes) -> tuple[object, str]:
    try:
        return json.loads(body.decode("utf-8")), "json"
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            return body.decode("utf-8"), "text"
        except UnicodeDecodeError:
            return base64.b64encode(body).decode("ascii"), "base64"


def _as_datetime(value: datetime | None) -> datetime | None:
    return value
