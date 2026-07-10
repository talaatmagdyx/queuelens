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
    kind: str = "dlq"  # dlq | parking | retry | normal
    publish_rate: float | None = None


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


@dataclass(frozen=True, slots=True)
class MessageRecord:
    fingerprint: str
    source_queue: str
    body: bytes
    payload: object
    payload_format: str
    payload_size: int
    content_type: str | None
    message_id: str | None
    correlation_id: str | None
    timestamp: datetime | None
    exchange: str
    routing_key: str
    headers: dict[str, Any]
    properties: dict[str, Any]
    redelivered: bool
    x_death: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReplayTarget:
    type: str
    queue: str | None = None
    exchange: str | None = None
    routing_key: str | None = None
