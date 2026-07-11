import json as _json
import logging
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

from sqlalchemy import delete, desc, func, select

from app.domain.models import AuditEntry
from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.models import AuditEventModel


def _audit_logger() -> logging.Logger:
    """Stdout JSON-lines logger — uvicorn does not equip app loggers with handlers."""
    logger = logging.getLogger("queuelens.audit")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("queuelens.audit %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


class AuditRepository:
    def __init__(self, database: Database) -> None:
        self._database = database
        # When on, every audit event is also emitted as a JSON line on the app log
        # (docker logs / stdout) so syslog- and log-shippers can pick it up.
        self.stream_to_log = False

    async def record(self, entry: AuditEntry) -> dict[str, object]:
        async with self._database.session() as session:
            model = AuditEventModel(
                timestamp=entry.timestamp,
                username=entry.username,
                action=entry.action,
                source_queue=entry.source_queue,
                message_fingerprint=entry.message_fingerprint,
                payload_hash=entry.payload_hash,
                target_type=entry.target_type,
                target_exchange=entry.target_exchange,
                target_queue=entry.target_queue,
                target_routing_key=entry.target_routing_key,
                result=entry.result,
                error_message=entry.error_message,
                request_ip=entry.request_ip,
                user_agent=entry.user_agent,
                metadata_json=entry.metadata,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            record = self._to_dict(model)
            if self.stream_to_log:
                _audit_logger().info(_json.dumps(record, default=str))
            return record

    async def list(
        self,
        *,
        action: str | None = None,
        username: str | None = None,
        source_queue: str | None = None,
        result: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        statement = select(AuditEventModel).order_by(desc(AuditEventModel.timestamp)).limit(limit)
        if action:
            statement = statement.where(AuditEventModel.action == action)
        if username:
            statement = statement.where(AuditEventModel.username == username)
        if source_queue:
            statement = statement.where(AuditEventModel.source_queue == source_queue)
        if result:
            statement = statement.where(AuditEventModel.result == result)

        async with self._database.session() as session:
            rows = (await session.scalars(statement)).all()
        return [self._to_dict(row) for row in rows]

    async def count(self, *, result: str, since: datetime) -> int:
        statement = (
            select(func.count())
            .select_from(AuditEventModel)
            .where(AuditEventModel.result == result, AuditEventModel.timestamp >= since)
        )
        async with self._database.session() as session:
            return int(await session.scalar(statement) or 0)

    @staticmethod
    def _to_dict(model: AuditEventModel) -> dict[str, object]:
        return {
            "id": model.id,
            "timestamp": model.timestamp.isoformat() if model.timestamp else None,
            "username": model.username,
            "action": model.action,
            "source_queue": model.source_queue,
            "message_fingerprint": model.message_fingerprint,
            "payload_hash": model.payload_hash,
            "target_type": model.target_type,
            "target_exchange": model.target_exchange,
            "target_queue": model.target_queue,
            "target_routing_key": model.target_routing_key,
            "result": model.result,
            "error_message": model.error_message,
            "request_ip": model.request_ip,
            "user_agent": model.user_agent,
            "metadata": model.metadata_json,
        }


    async def iter_all(
        self, batch_size: int = 500
    ) -> "AsyncIterator[dict[str, object]]":
        """Yield every audit event, oldest first, in batches (for exports)."""
        offset = 0
        while True:
            async with self._database.session() as session:
                rows = (
                    (
                        await session.execute(
                            select(AuditEventModel)
                            .order_by(AuditEventModel.timestamp, AuditEventModel.id)
                            .offset(offset)
                            .limit(batch_size)
                        )
                    )
                    .scalars()
                    .all()
                )
            if not rows:
                return
            for row in rows:
                yield self._to_dict(row)
            offset += batch_size

    async def delete_older_than(self, days: int) -> int:
        """Retention cleanup: drop audit rows older than N days."""
        from datetime import UTC, datetime

        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._database.session() as session:
            result = await session.execute(
                delete(AuditEventModel).where(AuditEventModel.timestamp < cutoff)
            )
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)
