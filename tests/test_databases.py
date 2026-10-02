"""The persistence layer on every supported database. PostgreSQL runs when
QUEUELENS_TEST_POSTGRES_URL names a throwaway database: each test drops its tables."""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.copy_db import copy
from app.domain.models import AuditEntry
from app.infrastructure.persistence.audit_repository import AuditRepository
from app.infrastructure.persistence.database import Database
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
