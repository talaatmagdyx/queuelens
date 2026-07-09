import hashlib
import json
from datetime import datetime
from typing import Any


def message_fingerprint(
    *,
    queue: str,
    body: bytes,
    headers: dict[str, Any],
    message_id: str | None,
    timestamp: datetime | None,
    exchange: str,
    routing_key: str,
) -> str:
    identity = {
        "queue": queue,
        "body": hashlib.sha256(body).hexdigest(),
        "headers": headers,
        "message_id": message_id,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "exchange": exchange,
        "routing_key": routing_key,
    }
    encoded = json.dumps(identity, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

