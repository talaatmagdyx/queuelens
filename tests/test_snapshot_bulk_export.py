"""From a snapshot: bulk-select every message a filter matches, and export the messages."""

import csv
import io
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.infrastructure.rabbitmq.message_browser import Scan
from app.main import create_app
from tests import cred
from tests.test_snapshots import _record

SECRET = cred()  # generated per run: a masked field must never reach an export


def _app(tmp_path, count: int = 250):
    app = create_app(Settings(
        auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/x.db",
        environments_json='{"staging": {"vhosts": ["/"]}}'))

    class Messages:
        async def snapshot(self, queue: str, depth: int) -> Scan:
            records = [_record(i) for i in range(count)]
            records[3] = _record(3, body=b"needle in here", payload_format="text")
            records[4] = _record(4, body=b"=HYPERLINK(\"http://x\")", payload_format="text")
            records[5] = _record(5, body=json.dumps({"password": SECRET, "n": 5}).encode())
            records[9] = _record(9, x_death=[{"count": 2}, {"count": 2}])
            return Scan(records, count, None)

    seen: dict[str, Any] = {}

    class Bulk:
        async def dry_run(self, **kwargs: Any) -> dict[str, object]:
            seen.clear()
            seen.update(kwargs)
            return {"batch_id": "b"}

    app.state.message_service = Messages()
    app.state.bulk_service = Bulk()
    app.state.environment_manager._bundles[("staging", "/")] = SimpleNamespace(started=True)
    return app, seen


async def _with_snapshot(app, run):
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sid = (await client.get("/api/queues/q.dlq/messages?snapshot=new")).json()[
                "snapshot"]["id"]
            return await run(client, sid)
    finally:
        await app.state.database.close()


@pytest.mark.asyncio
async def test_select_all_matching_picks_every_match_from_the_snapshot(tmp_path) -> None:
    app, seen = _app(tmp_path)
    picked: dict[str, Any] = {}

    async def run(client, sid):
        async def dry(match, **extra):
            response = await client.post("/api/messages/bulk/dry-run", json={
                "source_queue": "q.dlq", "action": "park", "snapshot": sid, "match": match,
                **extra})
            return response.status_code, set(seen.get("selected_fingerprints") or ())

        picked["needle"] = await dry({"contains": "NEEDLE"})
        picked["dead"] = await dry({"min_deaths": 3})
        picked["all"] = await dry({})
        picked["both"] = (await dry({}, fingerprints=[f"{1:064d}"]))[0]
        picked["expired"] = (await client.post("/api/messages/bulk/dry-run", json={
            "source_queue": "q.dlq", "action": "park", "snapshot": "gone", "match": {}})
        ).status_code
        picked["no_snapshot"] = (await client.post("/api/messages/bulk/dry-run", json={
            "source_queue": "q.dlq", "action": "park", "match": {}})).status_code
        return picked

    await _with_snapshot(app, run)
    assert picked["needle"] == (200, {f"{3:064d}"})
    assert picked["dead"] == (200, {f"{9:064d}"})
    assert picked["all"][0] == 200 and len(picked["all"][1]) == 250
    assert (picked["both"], picked["expired"], picked["no_snapshot"]) == (400, 404, 400)


@pytest.mark.asyncio
async def test_select_all_matching_stays_within_the_bulk_limit(tmp_path) -> None:
    app, _ = _app(tmp_path, count=800)

    async def run(client, sid):
        return await client.post("/api/messages/bulk/dry-run", json={
            "source_queue": "q.dlq", "action": "park", "snapshot": sid, "match": {}})

    too_many = await _with_snapshot(app, run)
    assert too_many.status_code == 400 and "800 messages selected" in too_many.json()["detail"]


@pytest.mark.asyncio
async def test_a_snapshot_exports_as_json_and_csv_masked_and_audited(tmp_path) -> None:
    app, _ = _app(tmp_path)

    async def run(client, sid):
        url = f"/api/queues/q.dlq/snapshots/{sid}/export"
        return (await client.get(url), await client.get(url + "?format=csv"),
                await client.get(url + "?contains=needle"),
                await client.get(url, headers={"X-QueueLens-Environment": "staging"}))

    as_json, as_csv, filtered, elsewhere = await _with_snapshot(app, run)
    messages = as_json.json()
    assert len(messages) == 250 and messages[3]["payload"] == "needle in here"
    assert "attachment; filename=q.dlq-snapshot.json" in as_json.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(as_csv.text)))
    assert len(rows) == 250 and rows[9]["deaths"] == "4"
    assert rows[4]["payload"].startswith("'=")  # opens as text, never as a formula
    assert SECRET not in as_json.text and SECRET not in as_csv.text  # masked like the UI
    assert len(filtered.json()) == 1
    assert elsewhere.status_code == 404  # another environment's snapshot is never handed out
    exports = await app.state.audit_repository.list(action="export_snapshot")
    assert [row["metadata"]["messages"] for row in exports][-1] == 250
