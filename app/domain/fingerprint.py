import hashlib
import json
from datetime import datetime
from typing import Any

# Headers the broker rewrites while a message just sits in its queue: a quorum queue
# stamps x-delivery-count on every redelivery — every preview — so hashing it would give
# a message a new identity each time someone looked at it.
VOLATILE_HEADERS = frozenset({"x-delivery-count"})


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
        "headers": {k: v for k, v in headers.items() if k not in VOLATILE_HEADERS},
        "message_id": message_id,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "exchange": exchange,
        "routing_key": routing_key,
    }
    encoded = json.dumps(identity, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

