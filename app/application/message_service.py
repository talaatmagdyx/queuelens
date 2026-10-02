from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from app.domain.models import MessageRecord
from app.infrastructure.rabbitmq.message_browser import (
    SCAN_BYTES_BUDGET,
    MessageBrowser,
    Scan,
    with_payload,
)

# Raises before any basic.get when browsing the queue would be destructive; returns
# whether the queue must be scanned whole (quorum queues — see fetch()).
BrowseGuard = Callable[[str], Awaitable[bool]]


async def _no_guard(_queue: str) -> bool:
    return False


class MessageService:
    def __init__(self, browser: MessageBrowser, guard: BrowseGuard = _no_guard) -> None:
        self._browser = browser
        self._guard = guard

    async def list_messages(
        self, queue_name: str, limit: int, depth: int | None = None
    ) -> list[MessageRecord]:
        """The first `limit` messages. A quorum queue is read whole (up to `depth`)."""
        whole = bool(await self._guard(queue_name))
        scan = await self._browser.scan(
            queue_name, (depth or limit) if whole else limit, whole=whole
        )
        return scan.records[:limit]

    async def snapshot(self, queue_name: str, depth: int) -> Scan:
        """One scan down to `depth` (or the memory budget), kept slim for paging."""
        whole = bool(await self._guard(queue_name))
        return await self._browser.scan(
            queue_name, depth, whole=whole, max_bytes=SCAN_BYTES_BUDGET, slim=True
        )

    async def get_message(
        self, queue_name: str, fingerprint: str, limit: int, depth: int | None = None
    ) -> MessageRecord:
        messages = await self.list_messages(queue_name, limit, depth)
        matches = [message for message in messages if message.fingerprint == fingerprint]
        if len(matches) != 1:
            raise MessageNotUniquelyIdentifiable(
                f"Message {fingerprint} was not found uniquely in {queue_name}"
            )
        return matches[0]


class MessageNotUniquelyIdentifiable(LookupError):
    """The best-effort fingerprint did not identify exactly one message."""


MASKED_VALUE = "***"  # ASCII so Jinja's tojson doesn't escape it into • noise

_EMPTY: frozenset[str] = frozenset()


def message_to_dict(
    message: MessageRecord,
    max_message_size_bytes: int | None = None,
    masked_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    message = with_payload(message)  # snapshot records keep raw bodies only
    masked = frozenset(_normalize_key(field) for field in masked_fields)
    payload: Any = _jsonable(message.payload, masked)
    # the raw (still-compressed) bytes carry every value masking hides and the whole
    # body truncation drops — only expose them when neither applies
    payload_encoded = message.payload_encoded
    if masked and payload != _jsonable(message.payload):
        payload_encoded = None
    payload_truncated = False
    if max_message_size_bytes is not None and message.payload_size > max_message_size_bytes:
        payload = f"[payload truncated at {max_message_size_bytes} bytes]"
        payload_truncated = True
        payload_encoded = None
    return {
        "fingerprint": message.fingerprint,
        "queue": message.source_queue,
        "payload": payload,
        "payload_truncated": payload_truncated,
        "payload_format": message.payload_format,
        "payload_size": message.payload_size,
        "decoded_from": message.decoded_from,
        "payload_encoded": payload_encoded,
        "content_type": message.content_type,
        "message_id": message.message_id,
        "correlation_id": message.correlation_id,
        "timestamp": message.timestamp.isoformat() if message.timestamp else None,
        "exchange": message.exchange,
        "routing_key": message.routing_key,
        "headers": _jsonable(message.headers, masked),
        "properties": _jsonable(message.properties, masked),
        "redelivered": message.redelivered,
        "x_death": _jsonable(message.x_death),
    }


def _normalize_key(key: str) -> str:
    """Compare keys ignoring case and -/_ so "API-Key", "api_key", and
    "apiKey" all match a configured "api_key"."""
    return key.strip().lower().replace("-", "").replace("_", "")


def _jsonable(value: Any, masked: frozenset[str] = _EMPTY) -> Any:
    """Make broker data renderable and safe: datetimes (x-death "time") and
    raw bytes become strings, and values under configured sensitive keys are
    replaced. Display-only — the original message and replay payload are
    built from MessageRecord, never from this dict."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {
            key: MASKED_VALUE
            if masked and _normalize_key(str(key)) in masked
            else _jsonable(item, masked)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_jsonable(item, masked) for item in value]
    return value
