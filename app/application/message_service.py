from typing import Any

from app.domain.models import MessageRecord
from app.infrastructure.rabbitmq.message_browser import MessageBrowser


class MessageService:
    def __init__(self, browser: MessageBrowser) -> None:
        self._browser = browser

    async def list_messages(self, queue_name: str, limit: int) -> list[MessageRecord]:
        return await self._browser.list_messages(queue_name, limit)

    async def get_message(self, queue_name: str, fingerprint: str, limit: int) -> MessageRecord:
        messages = await self._browser.list_messages(queue_name, limit)
        for message in messages:
            if message.fingerprint == fingerprint:
                return message
        raise LookupError(f"Message {fingerprint} was not found in {queue_name}")


def message_to_dict(message: MessageRecord) -> dict[str, Any]:
    return {
        "fingerprint": message.fingerprint,
        "queue": message.source_queue,
        "payload": message.payload,
        "payload_format": message.payload_format,
        "payload_size": message.payload_size,
        "content_type": message.content_type,
        "message_id": message.message_id,
        "correlation_id": message.correlation_id,
        "timestamp": message.timestamp.isoformat() if message.timestamp else None,
        "exchange": message.exchange,
        "routing_key": message.routing_key,
        "headers": message.headers,
        "properties": message.properties,
        "redelivered": message.redelivered,
        "x_death": message.x_death,
    }

