from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.persistence.models import Base


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    # Additive column migrations for databases created by earlier releases.
    # create_all only creates missing tables — it never alters existing ones.
    MIGRATIONS = (
        "ALTER TABLE alert_rules ADD COLUMN fired BOOLEAN NOT NULL DEFAULT 0",
    )

    async def start(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        for statement in self.MIGRATIONS:
            try:
                async with self.engine.begin() as connection:
                    from sqlalchemy import text

                    await connection.execute(text(statement))
            except Exception:  # noqa: BLE001 - column already exists
                pass

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session:
            yield session

