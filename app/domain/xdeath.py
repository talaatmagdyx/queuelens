from typing import Any


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

