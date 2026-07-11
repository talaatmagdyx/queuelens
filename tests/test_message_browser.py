from datetime import UTC, datetime

import httpx
import pytest

from app.application.message_service import (
    MessageNotUniquelyIdentifiable,
    MessageService,
    message_to_dict,
)
from app.config import Settings
from app.domain.fingerprint import message_fingerprint
from app.domain.models import MessageRecord
from app.domain.xdeath import parse_x_death
from app.infrastructure.rabbitmq.message_browser import MessageBrowser
from app.main import create_app


def test_fingerprint_is_stable_and_xdeath_is_normalized() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    first = message_fingerprint(
        queue="orders.dlq",
        body=b"payload",
        headers={"x-death": [{"queue": "orders"}]},
        message_id="message-1",
        timestamp=timestamp,
        exchange="orders",
        routing_key="created",
    )
    second = message_fingerprint(
        queue="orders.dlq",
        body=b"payload",
        headers={"x-death": [{"queue": "orders"}]},
        message_id="message-1",
        timestamp=timestamp,
        exchange="orders",
        routing_key="created",
    )

    assert first == second
    assert len(first) == 64
    assert parse_x_death({"x-death": [{"routing-keys": "created"}]}) == [
        {"routing-keys": ["created"]}
    ]


def test_message_payload_preview_is_limited() -> None:
    message = MessageRecord(
        fingerprint="a" * 64,
        source_queue="orders.dlq",
        body=b"large",
        payload={"large": True},
        payload_format="json",
        payload_size=100,
        content_type="application/json",
        message_id=None,
        correlation_id=None,
        timestamp=None,
        exchange="orders",
        routing_key="created",
        headers={},
        properties={},
        redelivered=False,
    )

    rendered = message_to_dict(message, max_message_size_bytes=10)

    assert rendered["payload_truncated"] is True
    assert rendered["payload"] == "[payload truncated at 10 bytes]"


@pytest.mark.asyncio
async def test_detail_lookup_rejects_duplicate_best_effort_matches() -> None:
    message = MessageRecord(
        fingerprint="a" * 64,
        source_queue="orders.dlq",
        body=b"{}",
        payload={},
        payload_format="json",
        payload_size=2,
        content_type="application/json",
        message_id=None,
        correlation_id=None,
        timestamp=None,
        exchange="orders",
        routing_key="created",
        headers={},
        properties={},
        redelivered=False,
    )

    class DuplicateBrowser:
        async def list_messages(self, _queue: str, _limit: int) -> list[MessageRecord]:
            return [message, message]

    with pytest.raises(MessageNotUniquelyIdentifiable):
        await MessageService(DuplicateBrowser()).get_message(
            "orders.dlq", message.fingerprint, 100
        )


class FakeMessage:
    body = b'{"request_id":"req-1"}'
    headers = {"x-death": [{"reason": "rejected"}]}
    timestamp = None
    message_id = "message-1"
    content_type = "application/json"
    content_encoding = None
    delivery_mode = 2
    priority = 0
    correlation_id = "corr-1"
    reply_to = None
    expiration = None
    type = "order.created"
    user_id = None
    app_id = "orders"
    exchange = "orders.exchange"
    routing_key = "orders.created"
    redelivered = False
    processed = False

    def __init__(self) -> None:
        self.nacked = False

    async def nack(self, requeue: bool = False) -> None:
        self.nacked = requeue
        self.processed = True


class FakeChannel:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages

    async def declare_queue(self, _queue_name: str, passive: bool = False) -> "FakeChannel":
        assert passive is True
        return self

    async def get(self, no_ack: bool = False, fail: bool = False) -> FakeMessage | None:
        assert no_ack is False
        assert fail is False
        return self.messages.pop(0) if self.messages else None

    async def close(self) -> None:
        return None


class FakeChannelContext:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    async def __aenter__(self) -> FakeChannel:
        return self.channel

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeConnection:
    def __init__(self, channel: FakeChannelContext) -> None:
        self._channel = channel

    def channel(self) -> FakeChannelContext:
        return self._channel


@pytest.mark.asyncio
async def test_browser_requeues_every_message_after_preview() -> None:
    message = FakeMessage()
    browser = MessageBrowser(FakeConnection(FakeChannelContext(FakeChannel([message]))))

    records = await browser.list_messages("orders.dlq", limit=10)

    assert len(records) == 1
    assert records[0].payload == {"request_id": "req-1"}
    assert records[0].payload_format == "json"
    assert records[0].x_death == [{"reason": "rejected"}]
    assert message.nacked is True


@pytest.mark.asyncio
async def test_message_route_returns_message_detail() -> None:
    message = MessageRecord(
        fingerprint="a" * 64,
        source_queue="orders.dlq",
        body=b"{}",
        payload={},
        payload_format="json",
        payload_size=2,
        content_type="application/json",
        message_id="message-1",
        correlation_id=None,
        timestamp=None,
        exchange="orders",
        routing_key="created",
        headers={},
        properties={},
        redelivered=False,
    )

    class FakeMessageService:
        async def get_message(self, _queue: str, _fingerprint: str, _limit: int) -> MessageRecord:
            return message

        async def list_messages(self, _queue: str, _limit: int) -> list[MessageRecord]:
            return [message]

    app = create_app(Settings(auth_enabled=False))
    app.state.message_service = FakeMessageService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/queues/orders.dlq/messages/{message.fingerprint}")

    assert response.status_code == 200
    assert response.json()["message"] == message_to_dict(message)


class TestCompressedPayloads:
    """content_encoding-aware decode: gzip/deflate bodies inflate for display,
    the original bytes stay available as base64."""

    # A production-shaped body: gzip-compressed JSON (like content_encoding: gzip
    # messages from lumedia.events.deadletters).
    def test_gzip_json_decodes_with_encoded_original(self) -> None:
        import base64
        import gzip
        import json

        from app.infrastructure.rabbitmq.message_browser import _decode_payload

        original = {"name": "post_inserted", "post_id": 42, "priority": 1}
        body = gzip.compress(json.dumps(original).encode())

        payload, fmt, decoded_from = _decode_payload(body, "gzip")
        assert payload == original
        assert fmt == "json"
        assert decoded_from == "gzip"
        # sanity: the same body without the encoding hint stays base64
        raw_payload, raw_fmt, raw_from = _decode_payload(body, None)
        assert raw_fmt == "base64"
        assert raw_from is None
        assert base64.b64decode(raw_payload) == body

    def test_deflate_and_raw_deflate_decode(self) -> None:
        import zlib

        from app.infrastructure.rabbitmq.message_browser import _decode_payload

        text = "plain text body"
        payload, fmt, decoded_from = _decode_payload(zlib.compress(text.encode()), "deflate")
        assert (payload, fmt, decoded_from) == (text, "text", "deflate")

        raw = zlib.compressobj(wbits=-15)
        body = raw.compress(text.encode()) + raw.flush()
        payload, fmt, decoded_from = _decode_payload(body, "deflate")
        assert (payload, fmt, decoded_from) == (text, "text", "deflate")

    def test_corrupt_gzip_falls_back_to_raw(self) -> None:
        from app.infrastructure.rabbitmq.message_browser import _decode_payload

        payload, fmt, decoded_from = _decode_payload(b"\x1f\x8bnot really gzip", "gzip")
        assert decoded_from is None
        assert fmt == "base64"

    def test_zip_bomb_is_left_encoded(self) -> None:
        import gzip

        from app.infrastructure.rabbitmq.message_browser import (
            MAX_DECODED_BYTES,
            _decode_payload,
        )

        bomb = gzip.compress(b"0" * (MAX_DECODED_BYTES + 1024))
        payload, fmt, decoded_from = _decode_payload(bomb, "gzip")
        assert decoded_from is None  # refused: would exceed the cap
        assert fmt == "base64"
