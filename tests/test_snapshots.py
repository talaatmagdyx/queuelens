"""Deep browsing (#4): one scan, many pages — and quorum queues read only whole."""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.application.queue_service import QueueService, UnsafeToBrowse
from app.config import Settings
from app.domain.models import MessageRecord
from app.infrastructure.rabbitmq.management_client import RabbitMQManagementClient
from app.infrastructure.rabbitmq.message_browser import (
    UNDECODED,
    Scan,
    fetch,
    requeue,
    with_payload,
)
from app.main import create_app


class Held:
    def __init__(self, n: int, size: int = 10) -> None:
        self.n, self.body, self.processed, self.nacked_at = n, b"x" * size, False, None

    async def nack(self, requeue: bool) -> None:
        self.processed = True
        Held.order.append(self.n)

    order: list[int] = []


class Queue:
    """basic.get from a list; the Declare-Ok reports `ready` (it may lag the list)."""

    def __init__(self, n: int, ready: int | None = None, size: int = 10) -> None:
        self.items = [Held(i, size) for i in range(n)]
        self.declaration_result = SimpleNamespace(message_count=n if ready is None else ready)

    async def get(self, no_ack: bool, fail: bool) -> Held | None:
        return self.items.pop(0) if self.items else None


@pytest.mark.asyncio
async def test_fetch_reads_quorum_queues_whole_or_not_at_all() -> None:
    held: list[Any] = []
    with pytest.raises(UnsafeToBrowse, match="only whole"):
        await fetch(Queue(12), "q.dlq", 10, held, whole=True)
    assert held == []  # refused before taking anything

    held = []
    with pytest.raises(UnsafeToBrowse):  # grew past the depth while it was read
        await fetch(Queue(12, ready=10), "q.dlq", 10, held, whole=True)
    assert len(held) == 11  # the caller still holds — and requeues — all of them

    held = []
    assert await fetch(Queue(10), "q.dlq", 10, held, whole=True) == (10, None)
    assert await fetch(Queue(10), "q.dlq", 4, [], whole=False) == (10, "depth")
    assert await fetch(Queue(10, size=100), "q.dlq", 10, [], max_bytes=250) == (10, "memory")
    assert await fetch(Queue(3), "q.dlq", 10, []) == (3, None)


@pytest.mark.asyncio
async def test_requeue_keeps_read_order() -> None:
    """Quorum queues put returned messages at the back in the order they come back."""
    Held.order = []
    messages: list[Any] = [Held(i) for i in range(5)]
    await requeue(messages, keep=messages[2])
    assert Held.order == [0, 1, 3, 4]


def _record(n: int, **extra: Any) -> MessageRecord:
    body = extra.pop("body", f'{{"n": {n}}}'.encode())
    return MessageRecord(
        fingerprint=f"{n:064d}", source_queue="q.dlq", body=body, payload=UNDECODED,
        payload_format=extra.pop("payload_format", "json"), payload_size=len(body),
        content_type="application/json", message_id=f"m{n}", correlation_id=None,
        timestamp=None, exchange="", routing_key="q.dlq", headers={}, properties={},
        redelivered=False, **extra,
    )


def test_slim_records_decode_on_demand() -> None:
    assert with_payload(_record(7)).payload == {"n": 7}


def _app(tmp_path, count: int = 250):
    app = create_app(Settings(
        auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db",
        environments_json='{"staging": {"vhosts": ["/"]}}'))
    calls: list[tuple[str, int]] = []

    class Messages:
        async def snapshot(self, queue: str, depth: int) -> Scan:
            calls.append((queue, depth))
            records = [_record(i) for i in range(count)]
            records[3] = _record(3, body=b"needle in here", payload_format="text")
            records[9] = _record(9, x_death=[{"count": 2}, {"count": 2}])
            return Scan(records, count + 10, "depth")

    class Actions:
        seen: dict[str, Any] = {}

        async def replay(self, **kwargs: Any) -> dict[str, object]:
            Actions.seen = kwargs
            return {"status": "success"}

    app.state.message_service = Messages()
    app.state.action_service = Actions()
    # staging resolves to a stand-in bundle: no broker connection from a unit test
    app.state.environment_manager._bundles[("staging", "/")] = SimpleNamespace(started=True)
    return app, calls, Actions


@pytest.mark.asyncio
async def test_a_snapshot_is_scanned_once_and_paged_and_filtered_from_memory(tmp_path) -> None:
    app, calls, _ = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = (await client.get("/api/queues/q.dlq/messages?snapshot=new&limit=50")).json()
        sid = first["snapshot"]["id"]

        async def page(query: str) -> dict[str, Any]:
            url = f"/api/queues/q.dlq/messages?snapshot={sid}&{query}"
            return dict((await client.get(url)).json())

        page5 = await page("offset=200&limit=50")
        found = await page("contains=NEEDLE")
        text = await page("payload_format=text")
        dead = await page("min_deaths=3")
        detail = await client.get(f"/api/queues/q.dlq/messages/{9:064d}?snapshot={sid}")
        expired = await client.get("/api/queues/q.dlq/messages?snapshot=nope")
        elsewhere = await client.get(f"/api/queues/q.dlq/messages?snapshot={sid}",
                                     headers={"X-QueueLens-Environment": "staging"})
    await app.state.database.close()

    assert calls == [("q.dlq", 5000)]  # one scan, at the default browse depth
    assert first["total"] == 250 and len(first["messages"]) == 50
    assert first["snapshot"] | {"id": "", "created_at": "", "expires_at": ""} == {
        "id": "", "created_at": "", "expires_at": "", "scanned": 250, "ready": 260,
        "complete": False, "stopped": "depth", "depth": 5000}
    assert [m["message_id"] for m in page5["messages"]] == [f"m{i}" for i in range(200, 250)]
    assert page5["messages"][0]["payload"] == {"n": 200}  # decoded per page
    assert [m["message_id"] for m in found["messages"]] == ["m3"]
    assert [m["message_id"] for m in text["messages"]] == ["m3"]
    assert [m["message_id"] for m in dead["messages"]] == ["m9"]
    assert detail.json()["message"]["message_id"] == "m9"  # from the snapshot, no broker read
    assert expired.status_code == 404 and "scan the queue again" in expired.json()["detail"]
    assert elsewhere.status_code == 404  # another environment never sees this copy


@pytest.mark.asyncio
async def test_an_action_reaches_a_message_deep_in_its_snapshot(tmp_path) -> None:
    app, _, actions = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    body = {"source_queue": "q.dlq", "fingerprint": f"{230:064d}", "mode": "move", "confirm": True}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        sid = (await client.get("/api/queues/q.dlq/messages?snapshot=new")).json()["snapshot"]["id"]
        await client.post("/api/messages/replay", json=body)
        plain = dict(actions.seen)
        await client.post("/api/messages/replay", json={**body, "snapshot": sid})
    await app.state.database.close()

    assert plain["max_scan"] == 100  # no snapshot: the refetch window
    assert actions.seen["max_scan"] == 230 + 1 + 100  # down to it, plus the window as slack
    assert actions.seen["depth"] == 5000


@pytest.mark.asyncio
async def test_a_bulk_selection_from_a_snapshot_reaches_deep_but_stays_capped(tmp_path) -> None:
    app, _, _ = _app(tmp_path, count=800)
    seen: dict[str, Any] = {}

    class Bulk:
        async def dry_run(self, **kwargs: Any) -> dict[str, object]:
            seen.update(kwargs)
            return {"batch_id": "b"}

    app.state.bulk_service = Bulk()
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        sid = (await client.get("/api/queues/q.dlq/messages?snapshot=new")).json()["snapshot"]["id"]
        picked = [f"{n:064d}" for n in (5, 700)]  # deeper than the 500-message bulk window
        await client.post("/api/messages/bulk/dry-run", json={
            "source_queue": "q.dlq", "action": "park", "fingerprints": picked, "snapshot": sid})
        too_many = await client.post("/api/messages/bulk/dry-run", json={
            "source_queue": "q.dlq", "action": "park",
            "fingerprints": [f"{n:064d}" for n in range(501)]})
    await app.state.database.close()

    assert seen["scan_limit"] == 700 + 1 + 100 and seen["max_bulk"] == 500
    assert too_many.status_code == 400 and "at most 500" in too_many.json()["detail"]


@pytest.mark.asyncio
async def test_the_guard_marks_quorum_queues_for_whole_scans() -> None:
    queues = {
        "classic.dlq": {"name": "classic.dlq", "type": "classic", "arguments": {}, "messages": 1},
        "quorum.dlq": {"name": "quorum.dlq", "type": "quorum", "messages": 1,
                       "arguments": {"x-delivery-limit": -1}},
        "huge.dlq": {"name": "huge.dlq", "type": "quorum", "messages": 9,
                     "message_bytes_ready": 65 * 1024 * 1024,
                     "arguments": {"x-delivery-limit": -1}},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/overview":
            return httpx.Response(200, json={"rabbitmq_version": "4.1.8"})
        return httpx.Response(200, json=queues[request.url.path.rsplit("/", 1)[-1]])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://m.test")
    service = QueueService(RabbitMQManagementClient(Settings(), client=client))
    assert await service.assert_browsable("classic.dlq") is False
    assert await service.assert_browsable("quorum.dlq") is True
    with pytest.raises(UnsafeToBrowse, match="65 MiB"):  # a whole scan would hold it all
        await service.assert_browsable("huge.dlq")
    await client.aclose()


def test_a_quorum_redelivery_does_not_change_a_messages_fingerprint() -> None:
    from app.domain.fingerprint import message_fingerprint

    def fp(headers: dict[str, Any]) -> str:
        return message_fingerprint(queue="q", body=b"{}", headers=headers, message_id="m",
                                   timestamp=None, exchange="", routing_key="q")

    seen = {fp({"a": 1}), fp({"a": 1, "x-delivery-count": 3}), fp({"a": 1, "x-delivery-count": 4})}
    assert len(seen) == 1
    assert fp({"a": 1}) != fp({"a": 2})
