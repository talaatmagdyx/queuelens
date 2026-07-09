from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass(frozen=True, slots=True)
class AuditEntry:
    username: str
    action: str
    timestamp: datetime
    source_queue: str | None = None
    message_fingerprint: str | None = None
    payload_hash: str | None = None
    target_type: str | None = None
    target_exchange: str | None = None
    target_queue: str | None = None
    target_routing_key: str | None = None
    result: str = "started"
    error_message: str | None = None
    request_ip: str | None = None
    user_agent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
