from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.persistence.coordination import lock_key
from app.infrastructure.persistence.models import Base

SCHEMA_LOCK = lock_key("queuelens", "schema")


class SchemaUpgradeError(RuntimeError):
    """A column a newer release needs could not be added: QueueLens doesn't start."""


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    # Columns added to a table after its first release. create_all only creates missing
    # tables, it never alters existing ones, so a database from an earlier release gets
    # these at startup. Add new columns here: tests/test_databases.py upgrades a database
    # that lacks every one of them.
    MIGRATIONS: tuple[tuple[str, str, str], ...] = (
        ("alert_rules", "fired", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("users", "must_change_password", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("replay_policies", "environment", "VARCHAR(128)"),
        ("replay_policies", "vhost", "VARCHAR(255)"),
        ("alert_rules", "environment", "VARCHAR(128)"),
        ("alert_rules", "vhost", "VARCHAR(255)"),
    )

    async def start(self) -> None:
        async with self.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                # held until commit: replicas starting together would otherwise race
                # CREATE TABLE and ALTER TABLE
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK}
                )
            await connection.run_sync(self._create_and_upgrade)

    @classmethod
    def _create_and_upgrade(cls, connection: Connection) -> None:
        Base.metadata.create_all(connection)
        columns: dict[str, set[str]] = {}
        for table, column, ddl in cls.MIGRATIONS:
            if table not in columns:
                columns[table] = {c["name"] for c in inspect(connection).get_columns(table)}
            if column in columns[table]:
                continue
            statement = f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
            try:
                connection.execute(text(statement))
            except Exception as error:
                raise SchemaUpgradeError(
                    f"Could not add the column {table}.{column} this release needs ({error}). "
                    f"Run `{statement}` as a database user allowed to alter tables, or give "
                    f"QueueLens's user that right, then start QueueLens again."
                ) from error
            columns[table].add(column)

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session:
            yield session

