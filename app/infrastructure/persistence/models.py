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



class AppSettingModel(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AlertRuleModel(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    pattern: Mapped[str] = mapped_column(String(255), default="*")
    metric: Mapped[str] = mapped_column(String(32), default="messages_ready")
    operator: Mapped[str] = mapped_column(String(4), default=">")
    threshold: Mapped[int] = mapped_column(Integer, default=100)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    severity: Mapped[str] = mapped_column(String(16), default="Warning")
    channels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"list": [...]}
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fired: Mapped[bool] = mapped_column(default=False)  # condition currently held


class NotificationModel(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    level: Mapped[str] = mapped_column(String(16), default="Info")  # Alert|Warning|Info|Success
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(64), default="Alert Engine")
    rule_id: Mapped[int | None] = mapped_column(Integer, index=True)
    delivery: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # per-channel outcomes


class UserModel(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(128), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default="Operator")
    email: Mapped[str | None] = mapped_column(String(255))
    invited_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    active: Mapped[bool] = mapped_column(default=True)


class BulkBatchModel(Base):
    __tablename__ = "bulk_batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
