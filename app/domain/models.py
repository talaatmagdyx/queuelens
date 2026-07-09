from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class QueueInfo:
    name: str
    vhost: str
    messages: int
    messages_ready: int
    messages_unacked: int
    consumers: int
    durable: bool
    arguments: dict[str, Any] = field(default_factory=dict)
    is_dlq: bool = False

