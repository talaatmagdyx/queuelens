from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.persistence.coordination import lock_key
from app.infrastructure.persistence.models import Base

SCHEMA_LOCK = lock_key("queuelens", "schema")


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    # Additive column migrations for databases created by earlier releases.
    # create_all only creates missing tables — it never alters existing ones.
    MIGRATIONS = (
        "ALTER TABLE alert_rules ADD COLUMN fired BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE replay_policies ADD COLUMN environment VARCHAR(128)",
        "ALTER TABLE replay_policies ADD COLUMN vhost VARCHAR(255)",
        "ALTER TABLE alert_rules ADD COLUMN environment VARCHAR(128)",
        "ALTER TABLE alert_rules ADD COLUMN vhost VARCHAR(255)",
    )

    async def start(self) -> None:
        async with self.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                # replicas starting together on an empty database would race CREATE TABLE
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK}
                )
            await connection.run_sync(Base.metadata.create_all)
        for statement in self.MIGRATIONS:
            try:
                async with self.engine.begin() as connection:
                    await connection.execute(text(statement))
            except Exception:  # noqa: BLE001 - column already exists
                pass

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session:
            yield session

