"""Replay policies: which messages a run picks, what it does with them, and who may run it."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.application.bulk_service import BulkBatch
from app.application.replay_policies import PolicyRunner, plan
from app.config import Settings
from app.domain.models import ReplayTarget
from app.infrastructure.rabbitmq.message_browser import Scan
from app.main import create_app
from tests import cred
from tests.test_snapshots import _record

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
PW = {name: cred() for name in ("admin", "ops")}


def _dead(n: int, deaths: int, minutes_ago: float, queue: str | None = "orders") -> Any:
    entry = {"count": deaths, "reason": "rejected", "time": NOW - timedelta(minutes=minutes_ago)}
    if queue:
        entry["queue"] = queue
    return _record(n, x_death=[entry])


def test_backoff_doubles_with_each_death_and_the_exhausted_are_parked() -> None:
    records = [
        _dead(1, 1, 4),    # backoff 5: waits
        _dead(2, 1, 6),    # due
        _dead(3, 2, 9),    # 10 for its second death: waits
        _dead(4, 2, 11),   # due
        _dead(5, 3, 0),    # exhausted at 3: parked, whatever its age
        _record(6),        # never died: no history
        _dead(7, 1, 60, queue="billing"),  # due, to its own origin
        _dead(8, 1, 60, queue=None),       # due, but the history names no queue
    ]
    out = plan(records, dlq="q.dlq", now=NOW, max_deaths=3, backoff_minutes=5, cap=100,
               fallback=None)
    origin = ReplayTarget(type="queue", queue="orders")
    assert out.replay == {origin: [f"{2:064d}", f"{4:064d}"],
                          ReplayTarget(type="queue", queue="billing"): [f"{7:064d}"]}
    assert out.park == [f"{5:064d}"]
    assert (out.waiting, out.no_history, out.no_target) == (2, 1, 1)


def test_the_configured_target_stands_in_and_the_cap_counts_oldest_first() -> None:
    fallback = ReplayTarget(type="queue", queue="orders.retry")
    records = [_dead(i, 1, 60, queue="q.dlq") for i in range(5)]  # died in the DLQ itself
    out = plan(records, dlq="q.dlq", now=NOW, max_deaths=3, backoff_minutes=5, cap=3,
               fallback=fallback)
    assert out.replay == {fallback: [f"{i:064d}" for i in range(3)]} and out.capped == 2


class FakeBulk:
    """Dry runs and executions without a broker; `fail` makes every publish fail."""

    def __init__(self) -> None:
        self.batches: dict[str, BulkBatch] = {}
        self.calls: list[tuple[str, str | None, list[str]]] = []
        self.fail = False

    async def dry_run(self, **kw: Any) -> dict[str, object]:
        batch = BulkBatch(
            id=f"b{len(self.batches)}", source_queue=kw["source_queue"], action=kw["action"],
            operator_action="move" if kw["action"] == "replay" else "park",
            target=kw["target"], fingerprints=kw["selected_fingerprints"],
            message_count=len(kw["selected_fingerprints"]), duplicate_fingerprints=0)
        self.batches[batch.id] = batch
        target = kw["target"].queue if kw["target"] else None
        self.calls.append((kw["action"], target, sorted(kw["selected_fingerprints"])))
        return {"batch_id": batch.id}

    async def peek_batch(self, batch_id: str) -> BulkBatch | None:
        return self.batches.get(batch_id)

    async def execute(self, batch_id: str, replay_headers: Any = None) -> tuple[BulkBatch, dict]:
        batch = self.batches.pop(batch_id)
        n = len(batch.fingerprints)
        status = "failed" if self.fail else "success"
        summary = {"succeeded": 0 if self.fail else n, "failed": n if self.fail else 0,
                   "skipped_duplicates": 0, "not_found": 0}
        results = [{"fingerprint": fp, "status": status} for fp in batch.fingerprints]
        return batch, {"summary": summary, "results": results}


def _app(tmp_path, records, consumers: dict[str, int]):
    app = create_app(Settings(auth_enabled=True, admin_password=PW["admin"],
                              users_json=f'{{"ops": "{PW["ops"]}"}}',
                              database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db"))

    class Messages:
        async def snapshot(self, queue: str, depth: int) -> Scan:
            return Scan(records, len(records), None)

    class Queues:
        async def get_queue(self, name: str) -> Any:
            if name not in consumers:
                raise LookupError(name)
            return SimpleNamespace(consumers=consumers[name])

    app.state.message_service = Messages()
    app.state.queue_service = Queues()
    app.state.bulk_service = FakeBulk()
    app.state.alert_engine.dispatch = _no_delivery
    app.state.policy_runner = PolicyRunner(app.state, clock=lambda: NOW)
    return app


async def _no_delivery(*_args: Any, **_kw: Any) -> dict[str, Any]:
    return {}


async def _policy(app, **overrides: Any) -> dict[str, Any]:
    fields = {"name": "orders", "queue": "q.dlq", "max_deaths": 3, "backoff_minutes": 5,
              "interval_minutes": 10, "cap": 100, "enabled": True, **overrides}
    return dict(await app.state.replay_policies.create("admin", **fields))


@pytest.mark.asyncio
async def test_a_run_replays_to_origins_with_consumers_and_parks_the_exhausted(tmp_path) -> None:
    records = [_dead(1, 1, 60), _dead(2, 1, 60, queue="billing"), _dead(3, 3, 1)]
    app = _app(tmp_path, records, {"orders": 1, "billing": 0})
    await app.state.database.start()
    try:
        result = await app.state.policy_runner.run(await _policy(app))
        bulk = app.state.bulk_service
        assert bulk.calls == [("replay", "orders", [f"{1:064d}"]), ("park", None, [f"{3:064d}"])]
        assert (result["replayed"], result["parked"], result["skipped_no_consumers"]) == (1, 1, 1)
        rows = {r["action"]: r for r in await app.state.audit_repository.list()}
        assert rows["run_replay_policy"]["username"] == "policy:orders"
        assert rows["replay"]["username"] == "policy:orders"  # per-message trail, as for a person
        assert (await app.state.replay_policies.get(1))["last_result"]["replayed"] == 1
    finally:
        await app.state.database.close()


@pytest.mark.asyncio
async def test_three_failed_runs_pause_the_policy_and_notify(tmp_path) -> None:
    app = _app(tmp_path, [_dead(1, 1, 60)], {"orders": 1})
    await app.state.database.start()
    try:
        app.state.bulk_service.fail = True
        policy = await _policy(app)
        for _ in range(3):
            await app.state.policy_runner.run(policy)
        stored = await app.state.replay_policies.get(policy["id"])
        assert (stored["enabled"], stored["consecutive_failures"]) == (False, 3)
        notes = await app.state.notifications.list()
        assert notes[0]["title"] == "Replay policy paused: orders"
        assert await app.state.audit_repository.list(action="pause_replay_policy")
    finally:
        await app.state.database.close()


@pytest.mark.asyncio
async def test_preview_moves_nothing_and_run_due_respects_the_interval(tmp_path) -> None:
    app = _app(tmp_path, [_dead(1, 1, 60), _dead(2, 4, 1)], {"orders": 2})
    await app.state.database.start()
    try:
        policy = await _policy(app)
        preview = await app.state.policy_runner.run(policy, preview=True)
        assert preview["targets"] == {"orders": {"due": 1, "consumers": 2}}
        assert preview["to_park"] == 1 and app.state.bulk_service.calls == []
        await app.state.policy_runner.run_due()  # never ran: due now
        await app.state.policy_runner.run_due()  # ran a moment ago: not again yet
        assert [call[0] for call in app.state.bulk_service.calls] == ["replay", "park"]
    finally:
        await app.state.database.close()


@pytest.mark.asyncio
async def test_only_the_leader_runs_policies(tmp_path) -> None:
    app = _app(tmp_path, [_dead(1, 1, 60)], {"orders": 1})
    await app.state.database.start()
    try:
        await _policy(app)

        async def not_leader() -> bool:
            return False

        runner = PolicyRunner(app.state, is_leader=not_leader, clock=lambda: NOW)
        runner.TICK_SECONDS = 0.01
        runner.start()
        await asyncio.sleep(0.1)
        await runner.stop()
        assert app.state.bulk_service.calls == []
    finally:
        await app.state.database.close()


@pytest.mark.asyncio
async def test_admins_manage_policies_and_operators_can_only_pause(tmp_path) -> None:
    app = _app(tmp_path, [_dead(1, 1, 60)], {"orders": 1, "q.dlq": 0})
    await app.state.database.start()
    body = {"name": "orders", "queue": "q.dlq"}
    admin, ops = ("admin", PW["admin"]), ("ops", PW["ops"])
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            assert (await http.post("/api/policies", json=body, auth=ops)).status_code == 403
            assert (await http.post("/api/policies", auth=admin,
                                    json={**body, "queue": "missing"})).status_code == 404
            created = (await http.post("/api/policies", json=body, auth=admin)).json()
            url = f"/api/policies/{created['id']}"
            assert (await http.post(url + "/preview", auth=ops)).status_code == 200
            assert (await http.post(url + "/run", auth=ops)).status_code == 403
            assert (await http.patch(url, json={"enabled": False}, auth=ops)).status_code == 200
            assert (await http.patch(url, json={"enabled": True}, auth=ops)).status_code == 403
            assert (await http.patch(url, json={"enabled": True}, auth=admin)).status_code == 200
            assert (await http.delete(url, auth=ops)).status_code == 403
            assert (await http.delete(url, auth=admin)).status_code == 200
        actions = [r["action"] for r in await app.state.audit_repository.list()]
        assert {"create_replay_policy", "update_replay_policy",
                "delete_replay_policy"} <= set(actions)
    finally:
        await app.state.database.close()


@pytest.mark.asyncio
async def test_metrics_count_what_policies_did_and_flag_a_paused_one(tmp_path) -> None:
    app = _app(tmp_path, [_dead(1, 1, 60)], {"orders": 1})
    await app.state.database.start()
    try:
        app.state.bulk_service.fail = True
        policy = await _policy(app, name="metered")
        for _ in range(3):
            await app.state.policy_runner.run(policy)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            text = (await http.get("/metrics", auth=("admin", PW["admin"]))).text
        assert 'queuelens_policy_paused{policy="metered"} 1.0' in text
        assert 'queuelens_policy_runs_total{policy="metered",result="partial"} 3.0' in text
        assert 'queuelens_policy_messages_total{outcome="failed",policy="metered"} 3.0' in text
    finally:
        await app.state.database.close()
