"""The persistence layer on every supported database. PostgreSQL runs when
QUEUELENS_TEST_POSTGRES_URL names a throwaway database: each test drops its tables."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, inspect

from app.copy_db import copy
from app.domain.models import AuditEntry
from app.infrastructure.persistence.audit_repository import AuditRepository
from app.infrastructure.persistence.database import Database, SchemaUpgradeError
from app.infrastructure.persistence.models import Base
from app.infrastructure.persistence.store import (
    AlertRuleRepository,
    BulkBatchRepository,
    SettingsRepository,
)

POSTGRES = os.environ.get("QUEUELENS_TEST_POSTGRES_URL")


@pytest.fixture(params=["sqlite", "postgres"])
async def url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite+aiosqlite:///{tmp_path}/target.db"
        return
    if not POSTGRES:
        pytest.skip("QUEUELENS_TEST_POSTGRES_URL not set")
    database = Database(POSTGRES)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await database.close()
    yield POSTGRES


@pytest.fixture
def far_from_utc(monkeypatch):
    # SQLite returns timestamps without a zone: read as local time they'd shift by the
    # copying machine's offset
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


async def _started(url: str) -> Database:
    database = Database(url)
    await database.start()
    return database


async def test_repositories_round_trip(url) -> None:
    database = await _started(url)
    try:
        audit = AuditRepository(database)
        row = await audit.record(AuditEntry(
            username="admin", action="replay", timestamp=datetime.now(UTC), result="success",
            source_queue="q" * 300,  # longer than the column: PostgreSQL would refuse the row
            metadata={"environment": "prod", "nested": {"n": [1, 2]}},
        ))
        assert row["source_queue"] == "q" * 255
        assert (await audit.list(action="replay"))[0]["metadata"]["nested"] == {"n": [1, 2]}

        settings = SettingsRepository(database)
        await settings.put({"ui": {"theme": "dark"}})
        assert await settings.get("ui") == {"theme": "dark"}

        batches = BulkBatchRepository(database)
        await batches.save("b1", {"n": 1}, datetime.now(UTC) + timedelta(minutes=5))
        taken = await asyncio.gather(batches.take("b1"), batches.take("b1"))
        assert sorted(taken, key=bool) == [None, {"n": 1}]  # one-shot, even when raced
    finally:
        await database.close()


async def test_alert_fires_once_when_evaluations_overlap(url) -> None:
    database = await _started(url)
    try:
        rules = AlertRuleRepository(database)
        rule = await rules.create(name="backlog", pattern="*.dlq")
        now = datetime.now(UTC)
        assert sorted(await asyncio.gather(*(rules.mark_fired(rule["id"], now)
                                              for _ in range(3)))) == [False, False, True]
        assert (await rules.list())[0]["fired"] is True
        assert sorted(await asyncio.gather(rules.set_fired(rule["id"], False),
                                           rules.set_fired(rule["id"], False))) == [False, True]
    finally:
        await database.close()


async def test_copy_db_moves_everything_and_ids_continue(url, tmp_path, far_from_utc) -> None:
    source_url = f"sqlite+aiosqlite:///{tmp_path}/source.db"
    source = await _started(source_url)
    audit = AuditRepository(source)
    first = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    for i in range(3):
        await audit.record(AuditEntry(username="ops", action="park", result="success",
                                      timestamp=first + timedelta(days=i)))
    await AlertRuleRepository(source).create(name="backlog", pattern="*.dlq")
    await SettingsRepository(source).put({"retention": {"days": 30}})
    await source.close()

    counts = await copy(source_url, url)
    assert counts["audit_events"] == 3 and counts["alert_rules"] == 1

    target = await _started(url)
    try:
        # rows arrived with their ids: new ones must not collide with them
        row = await AuditRepository(target).record(AuditEntry(
            username="ops", action="delete", timestamp=datetime.now(UTC), result="success"))
        assert row["id"] == 4
        assert await SettingsRepository(target).get("retention") == {"days": 30}
        parked = await AuditRepository(target).list(action="park")
        assert len(parked) == 3
        oldest = datetime.fromisoformat(str(parked[-1]["timestamp"]))
        assert oldest.replace(tzinfo=oldest.tzinfo or UTC) == first
    finally:
        await target.close()

    with pytest.raises(SystemExit, match="already has rows"):
        await copy(source_url, url)


def _before_migrations() -> MetaData:
    """Today's tables without the columns Database.MIGRATIONS adds: a database from the
    release before each of them. A new migration is covered here automatically."""
    added = {(table, column) for table, column, _ in Database.MIGRATIONS}
    old = MetaData()
    for table in Base.metadata.sorted_tables:
        Table(table.name, old,
              *(c._copy() for c in table.columns if (table.name, c.name) not in added))
    return old


async def test_a_database_from_an_earlier_release_upgrades_in_place(url) -> None:
    from app.config import Settings
    from app.main import _init_database, create_app

    old = _before_migrations()
    app = create_app(Settings(auth_enabled=False, database_url=url))
    database = app.state.database
    async with database.engine.begin() as connection:
        await connection.run_sync(old.create_all)
        await connection.execute(old.tables["alert_rules"].insert().values(
            name="backlog", pattern="*.dlq"))
        await connection.execute(old.tables["replay_policies"].insert().values(
            name="orders", queue="orders.dlq"))
        await connection.execute(old.tables["users"].insert().values(
            username="dana", password_hash="not-a-hash", role="Admin"))
    try:
        await _init_database(app)  # what startup does
        await _init_database(app)  # and every restart after: nothing left to add

        def columns(sync) -> dict[str, set[str]]:
            return {table: {c["name"] for c in inspect(sync).get_columns(table)}
                    for table, _, _ in Database.MIGRATIONS}

        async with database.engine.connect() as connection:
            present = await connection.run_sync(columns)
        assert all(column in present[table] for table, column, _ in Database.MIGRATIONS)
        default = app.state.environment_manager.default_key
        [rule] = await app.state.alert_rules.list()
        assert (rule["name"], rule["fired"]) == ("backlog", False)
        assert (rule["environment"], rule["vhost"]) == default  # it always watched these
        [policy] = await app.state.replay_policies.list()
        assert (policy["name"], policy["environment"], policy["vhost"]) == ("orders", *default)
        [dana] = [u for u in await app.state.users.list() if u["username"] == "dana"]
        assert (dana["role"], dana["must_change_password"]) == ("Admin", False)
    finally:
        await database.close()


async def test_a_column_that_cannot_be_added_stops_startup_and_says_which(url, monkeypatch) -> None:
    database = Database(url)
    await database.start()
    await AlertRuleRepository(database).create(name="backlog")
    # NOT NULL without a default can't be added to a table that has rows, on any database
    impossible = ("alert_rules", "impossible", "INTEGER NOT NULL")
    monkeypatch.setattr(Database, "MIGRATIONS", (*Database.MIGRATIONS, impossible))
    try:
        with pytest.raises(SchemaUpgradeError, match=r"alert_rules\.impossible"):
            await database.start()
    finally:
        await database.close()


async def test_replicas_starting_together_upgrade_an_old_database_once(url) -> None:
    if url.startswith("sqlite"):
        pytest.skip("one replica on SQLite")
    old = Database(url)
    async with old.engine.begin() as connection:
        await connection.run_sync(_before_migrations().create_all)
    await old.close()
    replicas = [Database(url) for _ in range(3)]
    try:  # the schema lock lets one add the columns; the others then find them there
        await asyncio.gather(*(replica.start() for replica in replicas))
    finally:
        for replica in replicas:
            await replica.close()


def test_every_column_added_since_the_last_release_has_a_migration() -> None:
    """tests/schema_released.json is the schema of the last release (v0.19.0). A column a
    table has gained since must be in Database.MIGRATIONS, or upgraded databases lack it.
    Refresh the file when a release adds a table, so its later columns are checked too."""
    released = json.loads((Path(__file__).parent / "schema_released.json").read_text())
    listed = {(table, column) for table, column, _ in Database.MIGRATIONS}
    missing = [(table.name, column.name) for table in Base.metadata.sorted_tables
               if table.name in released for column in table.columns
               if column.name not in released[table.name]
               and (table.name, column.name) not in listed]
    assert not missing, f"add these to Database.MIGRATIONS: {missing}"
