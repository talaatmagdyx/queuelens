"""Repositories for settings, alert rules, notifications, and users."""

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, desc, func, select, update

from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.models import (
    AlertRuleModel,
    AppSettingModel,
    LoginFailureModel,
    NotificationModel,
    UserModel,
)

# Settings keys whose values may carry secrets — encrypted at rest when a key is set.
SECRET_SETTING_KEYS = {"channels", "custom_environments"}


class SettingsRepository:
    """Server-persisted app settings (custom headers, channels, limits, retention, UI).

    When constructed with a Fernet key, values for SECRET_SETTING_KEYS are
    encrypted at rest; plaintext rows written before the key existed are still
    readable and get encrypted on their next write."""

    def __init__(self, database: Database, secret_key: str = "") -> None:
        self._database = database
        self._fernet = None
        if secret_key:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(secret_key.encode())

    def _encrypt(self, key: str, value: Any) -> Any:
        if self._fernet is None or key not in SECRET_SETTING_KEYS:
            return value
        import json as jsonlib

        token = self._fernet.encrypt(jsonlib.dumps(value).encode()).decode()
        return {"__encrypted__": token}

    def _decrypt(self, key: str, value: Any) -> Any:
        if not (isinstance(value, dict) and "__encrypted__" in value):
            return value
        if self._fernet is None:
            raise ValueError(
                f"Setting '{key}' is encrypted but QUEUELENS_SECRET_KEY is not configured"
            )
        import json as jsonlib

        return jsonlib.loads(self._fernet.decrypt(value["__encrypted__"].encode()))

    async def get_all(self) -> dict[str, Any]:
        async with self._database.session() as session:
            rows = (await session.execute(select(AppSettingModel))).scalars().all()
            return {row.key: self._decrypt(row.key, row.value) for row in rows}

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._database.session() as session:
            row = await session.get(AppSettingModel, key)
            return self._decrypt(key, row.value) if row is not None else default

    async def get_safe(self, key: str, default: Any = None) -> Any:
        """Like get(), but returns the default if the table does not exist yet."""
        try:
            return await self.get(key, default)
        except Exception:  # noqa: BLE001 - callers treat settings as best-effort
            return default

    async def put(self, values: dict[str, Any]) -> dict[str, Any]:
        async with self._database.session() as session:
            for key, value in values.items():
                stored = self._encrypt(key, value)
                row = await session.get(AppSettingModel, key)
                if row is None:
                    session.add(AppSettingModel(key=key, value=stored))
                else:
                    row.value = stored
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
            "fired": bool(row.fired),
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

    async def mark_fired(self, rule_id: int, at: datetime) -> bool:
        return await self.set_fired(rule_id, True, last_fired_at=at)

    async def set_fired(self, rule_id: int, fired: bool, **values: Any) -> bool:
        """Compare-and-set: True only for the caller that changed it, so evaluations that
        overlap (a slow pass, a second replica on a shared database) notify once."""
        async with self._database.session() as session:
            result = await session.execute(
                update(AlertRuleModel)
                .where(AlertRuleModel.id == rule_id, AlertRuleModel.fired != fired)
                .values(fired=fired, **values)
            )
            await session.commit()
            return bool(getattr(result, "rowcount", 0))


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


# Unknown or inactive users still pay one PBKDF2 round, so a failed login takes the
# same time whether or not the username exists (no enumeration by timing).
_TIMING_DECOY = hash_password(secrets.token_hex(16))


# A successful Basic-auth check is remembered briefly: every request carries the
# password, and PBKDF2 (~30 ms) on each one adds up. Only successes are cached, keyed
# by an HMAC under a random per-process key (never the password, never a plain hash).
# Each request still reads the user's row, and a hit counts only while the stored hash
# is the one it was verified against: a password changed or an account deactivated on
# any replica takes effect at once on all of them.
VERIFIED_FOR_SECONDS = 60.0


class LoginFailureRepository:
    """Failed logins, in the database so the limit holds however many replicas run."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def counts(self, keys: list[str], since: datetime) -> dict[str, int]:
        async with self._database.session() as session:
            rows = await session.execute(
                select(LoginFailureModel.key, func.count())
                .where(LoginFailureModel.key.in_(keys), LoginFailureModel.at > since)
                .group_by(LoginFailureModel.key)
            )
            return {key: int(count) for key, count in rows.all()}

    async def add(self, keys: list[str], at: datetime, forget_before: datetime) -> None:
        async with self._database.session() as session:
            session.add_all(LoginFailureModel(key=key, at=at) for key in keys)
            await session.execute(
                delete(LoginFailureModel).where(LoginFailureModel.at < forget_before)
            )
            await session.commit()


class UserRepository:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._verified: dict[tuple[str, bytes], tuple[str, float]] = {}
        self._cache_key = secrets.token_bytes(32)

    def _key(self, username: str, password: str) -> tuple[str, bytes]:
        mac = hmac.new(self._cache_key, password.encode(), "sha256")  # keyed lookup, not storage
        return username, mac.digest()

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
                    "must_change_password": bool(row.must_change_password),
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
        must_change_password: bool = True,
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
                    must_change_password=must_change_password,
                )
            )
            await session.commit()
            return True

    async def update(
        self, username: str, *, role: str | None = None, active: bool | None = None
    ) -> bool:
        """An Admin's change; every replica sees it on the account's next request."""
        async with self._database.session() as session:
            row = await session.get(UserModel, username)
            if row is None:
                return False
            if role is not None:
                row.role = role
            if active is not None:
                row.active = active
            await session.commit()
            return True

    async def delete(self, username: str) -> bool:
        async with self._database.session() as session:
            row = await session.get(UserModel, username)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def change_password(self, username: str, current: str, new: str) -> bool:
        async with self._database.session() as session:
            row = await session.get(UserModel, username)
            if row is None or not verify_password(current, row.password_hash):
                return False
            row.password_hash = hash_password(new)
            row.must_change_password = False
            await session.commit()
        return True

    async def verify(self, username: str, password: str) -> bool:
        async with self._database.session() as session:
            row = await session.get(UserModel, username)
        if row is None or not row.active:
            verify_password(password, _TIMING_DECOY)
            return False
        key = self._key(username, password)
        verified_hash, until = self._verified.get(key, ("", 0.0))
        if verified_hash == row.password_hash and until > time.monotonic():
            return True
        ok = verify_password(password, row.password_hash)
        if ok:
            self._verified[key] = (row.password_hash, time.monotonic() + VERIFIED_FOR_SECONDS)
        return ok


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class BulkBatchRepository:
    """Durable one-shot dry-run batches — they survive restarts and are safe to
    share once state moves out of process memory."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(self, batch_id: str, payload: dict[str, Any], expires_at: datetime) -> None:
        from app.infrastructure.persistence.models import BulkBatchModel

        async with self._database.session() as session:
            session.add(BulkBatchModel(id=batch_id, payload=payload, expires_at=expires_at))
            await session.commit()

    async def peek(self, batch_id: str) -> dict[str, Any] | None:
        from app.infrastructure.persistence.models import BulkBatchModel

        async with self._database.session() as session:
            row = await session.get(BulkBatchModel, batch_id)
            if row is None or _as_utc(row.expires_at) < datetime.now(UTC):
                return None
            return dict(row.payload)

    async def take(self, batch_id: str) -> dict[str, Any] | None:
        """One-shot: return and delete atomically."""
        from app.infrastructure.persistence.models import BulkBatchModel

        async with self._database.session() as session:
            # one statement: two executes racing for a batch can't both get it (SQLite
            # ignores SELECT ... FOR UPDATE)
            payload = await session.scalar(
                delete(BulkBatchModel)
                .where(BulkBatchModel.id == batch_id,
                       BulkBatchModel.expires_at >= datetime.now(UTC))
                .returning(BulkBatchModel.payload)
            )
            await session.commit()
            return dict(payload) if payload is not None else None

    async def prune_expired(self) -> int:
        from app.infrastructure.persistence.models import BulkBatchModel

        async with self._database.session() as session:
            result = await session.execute(
                delete(BulkBatchModel).where(BulkBatchModel.expires_at < datetime.now(UTC))
            )
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)
