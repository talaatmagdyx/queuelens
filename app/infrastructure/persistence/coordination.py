"""Coordination between QueueLens replicas that share a PostgreSQL database.

On SQLite there is one replica, and these locks are in-process only. On PostgreSQL any
number of replicas can run:
- a transaction-level advisory lock per queue keeps two replicas from reading or acting
  on one queue at once (a quorum queue would lose its order, an action could miss its
  message). It ends with its transaction, so no path can leave one held;
- a session-level advisory lock elects the one replica that evaluates alert rules. It
  ends with its connection, so another replica takes over when the leader dies.
"""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from app.infrastructure.persistence.database import Database

logger = logging.getLogger(__name__)


def lock_key(*names: str) -> int:
    """A signed 64-bit advisory-lock key for these names."""
    digest = hashlib.sha256("\0".join(names).encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


ALERT_LEADER = lock_key("queuelens", "alert-engine")


class Coordinator:
    def __init__(self, database: "Database | None" = None) -> None:
        self.shared = database is not None and database.engine.dialect.name == "postgresql"
        # Locks hold a connection for a whole broker operation (seconds, for a deep scan):
        # their own unpooled engine, so lock holders can never starve the app's pool of the
        # connections their own audit writes need.
        self._engine: AsyncEngine | None = (
            create_async_engine(database.engine.url, poolclass=NullPool)
            if self.shared and database is not None else None
        )
        self._local: dict[int, asyncio.Lock] = {}
        self._leader: AsyncConnection | None = None

    @asynccontextmanager
    async def lock(self, *names: str) -> AsyncIterator[None]:
        key = lock_key(*names)
        # waiters on this replica queue here, so each holds one database connection at most
        async with self._local.setdefault(key, asyncio.Lock()):
            if self._engine is None:
                yield
                return
            async with self._engine.connect() as connection, connection.begin():
                await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                yield

    async def is_leader(self) -> bool:
        """True while this replica holds the alert-engine lock; takes it when it's free."""
        if self._engine is None:
            return True
        try:
            if self._leader is None:
                connection = await self._engine.connect()
                won = await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": ALERT_LEADER}
                )
                await connection.commit()  # the lock is the session's: no transaction left open
                if not won:
                    await connection.close()
                    return False
                self._leader = connection
            else:
                await self._leader.scalar(text("SELECT 1"))  # still connected: still holding it
                await self._leader.commit()
            return True
        except Exception:  # noqa: BLE001 - lost the database: not the leader until it's back
            logger.warning("alert-engine leadership check failed", exc_info=True)
            await self._drop_leader()
            return False

    async def _drop_leader(self) -> None:
        if self._leader is not None:
            leader, self._leader = self._leader, None
            try:
                await leader.invalidate()  # never back into the pool still holding the lock
            except Exception:  # noqa: BLE001 - already gone
                pass

    async def close(self) -> None:
        await self._drop_leader()
        if self._engine is not None:
            await self._engine.dispose()
