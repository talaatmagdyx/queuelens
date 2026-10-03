"""Several replicas on one database: two app instances here stand in for two pods.
PostgreSQL runs when QUEUELENS_TEST_POSTGRES_URL names a throwaway database (each test
drops its tables); the cross-replica locks exist only there."""

import asyncio
import json
import os

import httpx
import pytest

from app.config import Settings
from app.infrastructure.persistence.coordination import Coordinator
from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.models import Base
from app.main import _init_database, _sync_settings, create_app
from tests import cred

POSTGRES = os.environ.get("QUEUELENS_TEST_POSTGRES_URL")
PW = {name: cred() for name in ("admin", "user", "new")}


async def _fresh_postgres() -> str:
    if not POSTGRES:
        pytest.skip("QUEUELENS_TEST_POSTGRES_URL not set")
    database = Database(POSTGRES)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await database.close()
    return POSTGRES


@pytest.fixture(params=["sqlite", "postgres"])
async def url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path}/shared.db"
    return await _fresh_postgres()


@pytest.fixture
async def replicas(url):
    apps = [create_app(Settings(auth_enabled=True, admin_password=PW["admin"],
                                database_url=url)) for _ in range(2)]
    for app in apps:
        await app.state.database.start()
    yield apps
    for app in apps:
        await app.state.coordinator.close()
        await app.state.database.close()


async def _me(app, auth) -> int:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return (await http.get("/api/me", auth=auth)).status_code


async def test_the_login_limit_counts_failures_on_every_replica(replicas) -> None:
    a, b = replicas
    for _ in range(10):
        assert await _me(a, ("admin", "wrong")) == 401
    assert await _me(b, ("admin", PW["admin"])) == 429  # throttled there too


async def test_a_password_changed_on_one_replica_ends_the_old_one_on_all(replicas) -> None:
    a, b = replicas
    await a.state.users.create(username="sam", password=PW["user"], role="Operator",
                               email=None, invited_by="admin", must_change_password=False)
    assert await _me(b, ("sam", PW["user"])) == 200  # now remembered on b
    transport = httpx.ASGITransport(app=a)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        changed = await http.post("/api/users/me/password", auth=("sam", PW["user"]), json={
            "current_password": PW["user"], "new_password": PW["new"]})
    assert changed.status_code == 200
    assert await _me(b, ("sam", PW["user"])) == 401
    assert await _me(b, ("sam", PW["new"])) == 200


async def test_an_environment_removed_on_one_replica_stops_working_on_all(replicas) -> None:
    a, b = replicas
    await a.state.settings_store.put({"custom_environments": {"extra": {"vhosts": ["/"]}}})
    await _sync_settings(b)
    assert b.state.environment_manager.scope("extra", None) == ("extra", "/")
    await a.state.settings_store.put({"custom_environments": {}})
    await _sync_settings(b)
    with pytest.raises(KeyError):
        b.state.environment_manager.scope("extra", None)


async def test_a_queue_lock_holds_across_replicas() -> None:
    url = await _fresh_postgres()
    databases = [Database(url), Database(url)]
    a, b = (Coordinator(database) for database in databases)
    events: list[str] = []

    async def first() -> None:
        async with a.lock("queue", "broker", "/", "orders.dlq"):
            events.append("a in")
            await asyncio.sleep(0.3)
            events.append("a out")

    async def second() -> None:
        await asyncio.sleep(0.05)
        async with b.lock("queue", "broker", "/", "billing.dlq"):
            events.append("b other queue")  # another queue doesn't wait
        async with b.lock("queue", "broker", "/", "orders.dlq"):
            events.append("b in")

    try:
        await asyncio.gather(first(), second())
    finally:
        for database in databases:
            await database.close()
    assert events == ["a in", "b other queue", "a out", "b in"]


async def test_one_replica_evaluates_alerts_and_another_takes_over() -> None:
    url = await _fresh_postgres()
    databases = [Database(url), Database(url)]
    a, b = (Coordinator(database) for database in databases)
    try:
        assert await a.is_leader() is True
        assert await b.is_leader() is False
        assert await a.is_leader() is True  # it keeps the lead
        await a.close()  # the leader goes away
        assert await b.is_leader() is True
        assert await a.is_leader() is False
    finally:
        await a.close()
        await b.close()
        for database in databases:
            await database.close()


async def test_the_alert_engine_waits_while_another_replica_leads(tmp_path) -> None:
    app = create_app(Settings(auth_enabled=False, alert_interval_seconds=0.01,
                              database_url=f"sqlite+aiosqlite:///{tmp_path}/a.db"))
    engine = app.state.alert_engine
    passes = 0

    async def evaluate_once() -> list[dict]:
        nonlocal passes
        passes += 1
        return []

    async def not_leader() -> bool:
        return False

    engine.evaluate_once = evaluate_once
    engine._is_leader = not_leader
    engine.start()
    await asyncio.sleep(0.1)
    await engine.stop()
    assert passes == 0


async def test_replicas_starting_together_on_an_empty_database() -> None:
    url = await _fresh_postgres()
    apps = [create_app(Settings(admin_password=PW["admin"], database_url=url,
                                users_json=json.dumps({"ops": PW["user"]}))) for _ in range(3)]
    try:
        await asyncio.gather(*(_init_database(app) for app in apps))
        accounts = sorted(u["username"] for u in await apps[0].state.users.list())
        assert accounts == ["admin", "ops"]  # seeded once, not three times or not at all
    finally:
        for app in apps:
            await app.state.coordinator.close()
            await app.state.database.close()
