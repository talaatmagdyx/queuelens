import json
from datetime import UTC, datetime

from app.application.message_service import MASKED_VALUE, message_to_dict
from app.config import Settings
from app.domain.models import MessageRecord


def record(**overrides: object) -> MessageRecord:
    defaults: dict = {
        "fingerprint": "f" * 64,
        "source_queue": "orders.dlq",
        "body": b"{}",
        "payload": {},
        "payload_format": "json",
        "payload_size": 2,
        "content_type": "application/json",
        "message_id": "m-1",
        "correlation_id": None,
        "timestamp": None,
        "exchange": "",
        "routing_key": "orders.dlq",
        "headers": {},
        "properties": {},
        "redelivered": False,
        "x_death": [],
    }
    defaults.update(overrides)
    return MessageRecord(**defaults)


def test_masks_sensitive_keys_in_payload_headers_and_properties() -> None:
    message = record(
        payload={
            "email": "user@example.com",
            "nested": {"api_key": "sk-123", "items": [{"password": "hunter2"}]},
            "order_id": "ord-1",
        },
        headers={"Authorization": "Bearer abc", "x-request-id": "r-1"},
        properties={"reply_to": "q", "access-token": "tok"},
    )

    result = message_to_dict(message, masked_fields=Settings().masked_field_names)

    assert result["payload"]["email"] == MASKED_VALUE
    assert result["payload"]["nested"]["api_key"] == MASKED_VALUE
    assert result["payload"]["nested"]["items"][0]["password"] == MASKED_VALUE
    assert result["payload"]["order_id"] == "ord-1"
    # key matching ignores case and -/_ separators
    assert result["headers"]["Authorization"] == MASKED_VALUE
    assert result["headers"]["x-request-id"] == "r-1"
    assert result["properties"]["access-token"] == MASKED_VALUE
    assert result["properties"]["reply_to"] == "q"


def test_masking_is_display_only_and_optional() -> None:
    message = record(payload={"email": "user@example.com"})

    unmasked = message_to_dict(message)
    disabled = message_to_dict(
        message, masked_fields=Settings(masking_enabled=False).masked_field_names
    )

    assert unmasked["payload"]["email"] == "user@example.com"
    assert disabled["payload"]["email"] == "user@example.com"
    # the record itself is untouched — replay reads MessageRecord, not the dict
    assert message.payload == {"email": "user@example.com"}


def test_masking_coexists_with_datetime_normalization() -> None:
    died_at = datetime(2026, 7, 10, tzinfo=UTC)
    message = record(
        headers={"x-death": [{"count": 1, "time": died_at}], "token": "t"},
        x_death=[{"count": 1, "time": died_at}],
    )

    result = message_to_dict(message, masked_fields=Settings().masked_field_names)

    assert result["headers"]["token"] == MASKED_VALUE
    assert result["headers"]["x-death"][0]["time"] == died_at.isoformat()
    assert result["x_death"][0]["time"] == died_at.isoformat()


def test_raw_view_is_withheld_when_it_would_reveal_masked_or_truncated_data() -> None:
    import base64
    import gzip

    def compressed(payload: dict) -> MessageRecord:
        raw = gzip.compress(json.dumps(payload).encode())
        return record(body=raw, payload=payload, payload_size=len(raw), decoded_from="gzip",
                      payload_encoded=base64.b64encode(raw).decode())

    secret = compressed({"password": "pw", "id": 1})
    harmless = compressed({"id": 1})
    assert message_to_dict(secret, masked_fields=("password",))["payload_encoded"] is None
    assert message_to_dict(secret)["payload_encoded"] is not None  # masking off: nothing hidden
    assert message_to_dict(harmless, masked_fields=("password",))["payload_encoded"] is not None
    assert message_to_dict(harmless, max_message_size_bytes=4)["payload_encoded"] is None
