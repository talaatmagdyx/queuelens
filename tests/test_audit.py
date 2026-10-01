from datetime import UTC, datetime

import httpx
import pytest

from app.config import Settings
from app.domain.models import AuditEntry
from app.infrastructure.persistence.audit_repository import AuditRepository
from app.infrastructure.persistence.database import Database
from app.main import create_app
from tests import cred

# generated per run; looked up by name so no line reads like a hardcoded secret
PW = {name: cred() for name in ("admin",)}



@pytest.mark.asyncio
async def test_audit_repository_records_and_filters(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/audit.db")
    await database.start()
    repository = AuditRepository(database)

    await repository.record(
        AuditEntry(
            username="admin",
            action="replay",
            timestamp=datetime.now(UTC),
            source_queue="orders.dlq",
            result="success",
        )
    )
    await repository.record(
        AuditEntry(
            username="admin",
            action="delete",
            timestamp=datetime.now(UTC),
            source_queue="orders.dlq",
            result="failed",
        )
    )

    events = await repository.list(action="replay")
    await database.close()

    assert len(events) == 1
    assert events[0]["action"] == "replay"
    assert events[0]["result"] == "success"


@pytest.mark.asyncio
async def test_audit_route_requires_basic_auth(tmp_path) -> None:
    settings = Settings(
        admin_username="admin",
        admin_password=PW["admin"],
        database_url=f"sqlite+aiosqlite:///{tmp_path}/api.db",
    )
    app = create_app(settings)
    await app.state.database.start()
    await app.state.audit_repository.record(
        AuditEntry(
            username="admin",
            action="login",
            timestamp=datetime.now(UTC),
            result="success",
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthenticated = await client.get("/api/audit")
        authenticated = await client.get("/api/audit", auth=("admin", PW["admin"]))

    await app.state.database.close()
    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["events"][0]["action"] == "login"



@pytest.mark.asyncio
async def test_audit_records_request_origin_and_csv_cannot_run_formulas(tmp_path) -> None:
    app = create_app(
        Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/o.db")
    )
    await app.state.database.start()

    class FailingActions:
        async def park(self, **_kwargs: object) -> None:
            raise RuntimeError("boom")

    app.state.action_service = FailingActions()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/messages/park", headers={"user-agent": "qa-agent/1.0"},
            json={"source_queue": '=HYPERLINK("http://x")', "fingerprint": "a" * 64,
                  "confirm": True},
        )
        events = (await client.get("/api/audit")).json()["events"]
        csv = (await client.get("/api/audit/export?format=csv")).text
    await app.state.database.close()

    assert events and all(
        e["request_ip"] == "127.0.0.1" and e["user_agent"] == "qa-agent/1.0" for e in events
    )
    assert '"\'=HYPERLINK' in csv and ',"=HYPERLINK' not in csv
