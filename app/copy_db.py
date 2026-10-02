"""Copy every QueueLens table into another database, e.g. from SQLite to PostgreSQL:

    python -m app.copy_db SOURCE_URL [TARGET_URL]

TARGET_URL defaults to QUEUELENS_DATABASE_URL, so inside the container the password stays
in the environment rather than on the command line:

    docker compose run --rm queuelens python -m app.copy_db sqlite+aiosqlite:///./data/queuelens.db

Stop QueueLens first so nothing writes during the copy. The target must be empty (its
tables are created when missing); the copy is one transaction and checks every table's
row count, so it either lands whole or not at all.
"""

import asyncio
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, text

from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.models import Base

BATCH = 1000


def _utc(row: dict[str, Any]) -> dict[str, Any]:
    # SQLite hands timestamps back without a zone; QueueLens wrote them as UTC
    return {
        key: value.replace(tzinfo=UTC) if isinstance(value, datetime) and not value.tzinfo
        else value
        for key, value in row.items()
    }


async def copy(source_url: str, target_url: str) -> dict[str, int]:
    if source_url == target_url:
        raise SystemExit("source and target are the same database")
    source, target = Database(source_url), Database(target_url)
    try:
        await source.start()  # brings a database from an older release up to the schema
        await target.start()
        tables = Base.metadata.sorted_tables
        counts: dict[str, int] = {}
        async with source.engine.connect() as src, target.engine.begin() as dst:
            for table in tables:
                if await dst.scalar(select(func.count()).select_from(table)):
                    raise SystemExit(f"{table.name} already has rows in the target: "
                                     "copy into an empty database")
            for table in tables:
                rows = await src.stream(select(table))
                async for chunk in rows.mappings().partitions(BATCH):
                    await dst.execute(insert(table), [_utc(dict(row)) for row in chunk])
                counts[table.name] = await src.scalar(select(func.count()).select_from(table)) or 0
                copied = await dst.scalar(select(func.count()).select_from(table))
                if copied != counts[table.name]:
                    raise SystemExit(f"{table.name}: copied {copied} of {counts[table.name]} rows")
                column = table.autoincrement_column
                if column is not None and dst.dialect.name == "postgresql":
                    # rows came with their ids, so the id sequence must continue after them
                    await dst.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', '{column.name}'), "
                        f"(SELECT max({column.name}) FROM {table.name}))"
                    ))
        return counts
    finally:
        await source.close()
        await target.close()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    from app.config import get_settings

    target_url = sys.argv[2] if len(sys.argv) == 3 else get_settings().database_url
    for name, rows in asyncio.run(copy(sys.argv[1], target_url)).items():
        print(f"{name}: {rows} rows")
