from typing import Any

# Stamped on every replay: how many times the message had died by then.
DEATHS_HEADER = "x-queuelens-deaths"


def deaths(x_death: list[dict[str, Any]], headers: dict[str, Any]) -> int:
    """How many times a message has died. RabbitMQ 3.x keeps adding to the x-death of a
    message a client republishes; 4.x starts it again at 1. So a replay stamps the count
    so far, and a message that carries the stamp has died at least once more since."""
    counted = sum(int(entry.get("count") or 0) for entry in x_death)
    try:
        stamped = int(headers.get(DEATHS_HEADER) or 0)
    except (TypeError, ValueError):  # a producer's header, not ours
        stamped = 0
    # ponytail: on 4.x a round trip that died several times counts once; exact needs the
    # broker version (3.x adds to x-death, 4.x restarts it)
    return max(counted, stamped + 1) if stamped > 0 else counted



def parse_x_death(headers: dict[str, Any]) -> list[dict[str, Any]]:
    raw = headers.get("x-death", [])
    if not isinstance(raw, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        routing_keys = normalized.get("routing-keys")
        if isinstance(routing_keys, str):
            normalized["routing-keys"] = [routing_keys]
        parsed.append(normalized)
    return parsed

