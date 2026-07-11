"""Repositories for settings, alert rules, notifications, and users."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, desc, select

from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.models import (
    AlertRuleModel,
    AppSettingModel,
    NotificationModel,
    UserModel,
)


class SettingsRepository:
    """Server-persisted app settings (custom headers, channels, limits, retention, UI)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_all(self) -> dict[str, Any]:
        async with self._database.session() as session:
            rows = (await session.execute(select(AppSettingModel))).scalars().all()
            return {row.key: row.value for row in rows}

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._database.session() as session:
            row = await session.get(AppSettingModel, key)
            return row.value if row is not None else default

    async def get_safe(self, key: str, default: Any = None) -> Any:
        """Like get(), but returns the default if the table does not exist yet."""
        try:
            return await self.get(key, default)
        except Exception:  # noqa: BLE001 - callers treat settings as best-effort
            return default

    async def put(self, values: dict[str, Any]) -> dict[str, Any]:
        async with self._database.session() as session:
            for key, value in values.items():
                row = await session.get(AppSettingModel, key)
                if row is None:
                    session.add(AppSettingModel(key=key, value=value))
                else:
                    row.value = value
            await session.commit()
        return await self.get_all()


class AlertRuleRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _to_dict(row: AlertRuleModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "pattern": row.pattern,
            "metric": row.metric,
            "operator": row.operator,
            "threshold": row.threshold,
            "duration_seconds": row.duration_seconds,
            "severity": row.severity,
            "channels": (row.channels or {}).get("list", []),
            "enabled": row.enabled,
            "created_by": row.created_by,
            "last_fired_at": row.last_fired_at.isoformat() if row.last_fired_at else None,
        }

    async def list(self) -> list[dict[str, Any]]:
        async with self._database.session() as session:
            rows = (
                (await session.execute(select(AlertRuleModel).order_by(desc(AlertRuleModel.id))))
                .scalars()
                .all()
            )
            return [self._to_dict(row) for row in rows]

    async def create(self, **fields: Any) -> dict[str, Any]:
        channels = fields.pop("channels", [])
        async with self._database.session() as session:
            row = AlertRuleModel(channels={"list": channels}, **fields)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._to_dict(row)

    async def update(self, rule_id: int, **fields: Any) -> dict[str, Any] | None:
        async with self._database.session() as session:
            row = await session.get(AlertRuleModel, rule_id)
            if row is None:
                return None
            if "channels" in fields:
                row.channels = {"list": fields.pop("channels")}
            for key, value in fields.items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return self._to_dict(row)

    async def delete(self, rule_id: int) -> bool:
        async with self._database.session() as session:
            row = await session.get(AlertRuleModel, rule_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def mark_fired(self, rule_id: int, at: datetime) -> None:
        async with self._database.session() as session:
            row = await session.get(AlertRuleModel, rule_id)
            if row is not None:
                row.last_fired_at = at
                await session.commit()


class NotificationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(
        self,
        *,
        level: str,
        title: str,
        message: str,
        source: str = "Alert Engine",
        rule_id: int | None = None,
        delivery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._database.session() as session:
            row = NotificationModel(
                timestamp=datetime.now(UTC),
                level=level,
                title=title,
                message=message,
                source=source,
                rule_id=rule_id,
                delivery=delivery or {},
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._to_dict(row)

    @staticmethod
    def _to_dict(row: NotificationModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "level": row.level,
            "title": row.title,
            "message": row.message,
            "source": row.source,
            "rule_id": row.rule_id,
            "delivery": row.delivery or {},
        }

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(NotificationModel)
                        .order_by(desc(NotificationModel.timestamp), desc(NotificationModel.id))
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [self._to_dict(row) for row in rows]

    async def purge_older_than(self, days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._database.session() as session:
            result = await session.execute(
                delete(NotificationModel).where(NotificationModel.timestamp < cutoff)
            )
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt=salt), stored)


class UserRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def seed_env_users(self, users: dict[str, str], admin_username: str) -> None:
        """Ensure env-configured accounts exist in the DB (idempotent)."""
        async with self._database.session() as session:
            for username, password in users.items():
                row = await session.get(UserModel, username)
                if row is None:
                    session.add(
                        UserModel(
                            username=username,
                            password_hash=hash_password(password),
                            role="Admin" if username == admin_username else "Operator",
                        )
                    )
            await session.commit()

    async def list(self) -> list[dict[str, Any]]:
        async with self._database.session() as session:
            rows = (
                (await session.execute(select(UserModel).order_by(UserModel.username)))
                .scalars()
                .all()
            )
            return [
                {
                    "username": row.username,
                    "role": row.role,
                    "email": row.email,
                    "invited_by": row.invited_by,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "active": row.active,
                }
                for row in rows
            ]

    async def create(
        self,
        *,
        username: str,
        password: str,
        role: str,
        email: str | None,
        invited_by: str,
    ) -> bool:
        async with self._database.session() as session:
            if await session.get(UserModel, username) is not None:
                return False
            session.add(
                UserModel(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    email=email,
                    invited_by=invited_by,
                )
            )
            await session.commit()
            return True

    async def verify(self, username: str, password: str) -> bool:
        async with self._database.session() as session:
            row = await session.get(UserModel, username)
            if row is None or not row.active:
                return False
            return verify_password(password, row.password_hash)
