from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    username: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    source_queue: Mapped[str | None] = mapped_column(String(255), index=True)
    message_fingerprint: Mapped[str | None] = mapped_column(String(128), index=True)
    payload_hash: Mapped[str | None] = mapped_column(String(128))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_exchange: Mapped[str | None] = mapped_column(String(255))
    target_queue: Mapped[str | None] = mapped_column(String(255))
    target_routing_key: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[str] = mapped_column(String(32), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    request_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

