"""End-to-end tests against a real RabbitMQ broker.

These exist because the mocked suite encoded the same wrong assumptions as
the code under test: fakes never dropped an unroutable publish and never put
a datetime inside x-death headers, so both phase-1 data-loss bugs passed a
green build. Everything here runs through a live broker.

The module skips itself when no broker is reachable. Start one with:

    docker compose up -d rabbitmq

Override the connection with QUEUELENS_IT_AMQP_URL / QUEUELENS_IT_MANAGEMENT_URL.
"""

import contextlib
import os
import socket
import uuid
from urllib.parse import urlparse

import aio_pika
import httpx
import pytest

from app.config import Settings
from app.main import create_app

AMQP_URL = os.environ.get(
    "QUEUELENS_IT_AMQP_URL", "amqp://queuelens:queuelens@localhost:5672/"
)
MANAGEMENT_URL = os.environ.get("QUEUELENS_IT_MANAGEMENT_URL", "http://localhost:15672")

_amqp = urlparse(AMQP_URL)
try:
    socket.create_connection((_amqp.hostname or "localhost", _amqp.port or 5672), timeout=1).close()
except OSError:
    pytest.skip(
        "RabbitMQ is not reachable; start it with `docker compose up -d rabbitmq`",
        allow_module_level=True,
    )


def _settings(tmp_path) -> Settings:
    return Settings(
        auth_enabled=False,
        rabbitmq_url=AMQP_URL,
        rabbitmq_management_url=MANAGEMENT_URL,
        rabbitmq_management_username=_amqp.username or "guest",
        rabbitmq_management_password=_amqp.password or "guest",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/integration.db",
        rabbitmq_operation_timeout_seconds=5,
    )


@pytest.mark.asyncio
async def test_browse_park_replay_delete_against_real_broker(tmp_path) -> None:
    suffix = uuid.uuid4().hex[:8]
    work = f"it.work.{suffix}"
    dlq = f"it.orders.dlq.{suffix}"
    replay_target = f"it.replay.{suffix}"
    parking = f"{dlq}.parking"
    missing_target = f"it.missing.{suffix}"

    connection = await aio_pika.connect_robust(AMQP_URL)
    channel = await connection.channel()
    try:
        await channel.declare_queue(dlq, durable=True)
        await channel.declare_queue(replay_target, durable=True)
        work_queue = await channel.declare_queue(
            work,
            durable=True,
            arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": dlq},
        )
        # Reject two messages so they dead-letter with REAL x-death headers,
        # including the datetime "time" field that broke the detail page.
        for index in range(2):
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=f'{{"order": {index}}}'.encode(),
                    message_id=f"it-{index}",
                    content_type="application/json",
                ),
                routing_key=work,
            )
        for _ in range(2):
            incoming = await work_queue.get(timeout=5)
            await incoming.reject(requeue=False)

        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Discovery sees the DLQ.
                queues = (await client.get("/api/queues", params={"dlq_only": True})).json()
                assert any(queue["name"] == dlq for queue in queues["queues"])

                # Browsing returns both messages with parsed x-death and is
                # non-destructive (a second listing sees the same messages).
                listing = (await client.get(f"/api/queues/{dlq}/messages")).json()["messages"]
                assert len(listing) == 2
                assert all(m["x_death"][0]["reason"] == "rejected" for m in listing)
                assert all(m["x_death"][0]["time"] for m in listing)
                by_id = {m["message_id"]: m["fingerprint"] for m in listing}
                assert set(by_id) == {"it-0", "it-1"}

                # Regression: the detail endpoint serializes datetime-bearing x-death.
                detail = await client.get(f"/api/queues/{dlq}/messages/{by_id['it-0']}")
                assert detail.status_code == 200
                assert detail.json()["message"]["x_death"][0]["reason"] == "rejected"

                # Regression: replay to a missing queue fails cleanly and the
                # message is NOT lost.
                failed_replay = await client.post(
                    "/api/messages/replay",
                    json={
                        "source_queue": dlq,
                        "fingerprint": by_id["it-0"],
                        "mode": "move",
                        "confirm": True,
                        "target": {"type": "queue", "queue": missing_target},
                    },
                )
                assert failed_replay.status_code == 404
                survivors = (await client.get(f"/api/queues/{dlq}/messages")).json()["messages"]
                assert len(survivors) == 2

                # Park creates the durable parking queue and moves the message.
                park = await client.post(
                    "/api/messages/park",
                    json={"source_queue": dlq, "fingerprint": by_id["it-0"], "confirm": True},
                )
                assert park.status_code == 200
                parking_queue = await channel.declare_queue(parking, passive=True)
                parked = await parking_queue.get(timeout=5)
                await parked.ack()
                assert parked.message_id == "it-0"

                # Replay-move to an existing queue stamps provenance headers.
                replay = await client.post(
                    "/api/messages/replay",
                    json={
                        "source_queue": dlq,
                        "fingerprint": by_id["it-1"],
                        "mode": "move",
                        "confirm": True,
                        "target": {"type": "queue", "queue": replay_target},
                    },
                )
                assert replay.status_code == 200
                target_queue = await channel.declare_queue(replay_target, passive=True)
                moved = await target_queue.get(timeout=5)
                await moved.ack()
                assert moved.message_id == "it-1"
                assert moved.headers["x-queuelens-replayed"] is True
                assert moved.headers["x-queuelens-source-queue"] == dlq

                # The DLQ is now empty and delete on a gone message conflicts.
                emptied = (await client.get(f"/api/queues/{dlq}/messages")).json()["messages"]
                assert emptied == []
                gone = await client.post(
                    "/api/messages/delete",
                    json={"source_queue": dlq, "fingerprint": by_id["it-1"], "confirm": True},
                )
                assert gone.status_code == 409

                # Every action wrote an attempt plus an outcome audit event.
                events = (await client.get("/api/audit", params={"source_queue": dlq})).json()
                results = [event["result"] for event in events["events"]]
                assert results.count("started") == 4
                assert results.count("success") == 2  # park + replay
                assert results.count("failed") == 2  # missing target + delete conflict
    finally:
        async with contextlib.AsyncExitStack() as stack:
            stack.push_async_callback(connection.close)
            cleanup = await connection.channel()
            for queue_name in (work, dlq, replay_target, parking, missing_target):
                with contextlib.suppress(Exception):
                    await cleanup.queue_delete(queue_name)


@pytest.mark.asyncio
async def test_bulk_dry_run_and_execute_against_real_broker(tmp_path) -> None:
    suffix = uuid.uuid4().hex[:8]
    dlq = f"it.bulk.dlq.{suffix}"
    parking = f"{dlq}.parking"

    connection = await aio_pika.connect_robust(AMQP_URL)
    channel = await connection.channel()
    try:
        await channel.declare_queue(dlq, durable=True)
        # three unique messages matching the filter, one that does not match,
        # and two byte-identical duplicates that must be skipped
        for index in range(3):
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=f'{{"tenant": "acme", "n": {index}}}'.encode(),
                    message_id=f"bulk-{index}",
                ),
                routing_key=dlq,
            )
        await channel.default_exchange.publish(
            aio_pika.Message(body=b'{"tenant": "globex"}', message_id="other"),
            routing_key=dlq,
        )
        for _ in range(2):
            # aio-pika auto-generates message_id, so byte-identical duplicates
            # need an explicit shared id to collide on fingerprint
            await channel.default_exchange.publish(
                aio_pika.Message(body=b'{"tenant": "acme", "dup": true}', message_id="dup"),
                routing_key=dlq,
            )

        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                preview = (
                    await client.post(
                        "/api/messages/bulk/dry-run",
                        json={
                            "source_queue": dlq,
                            "action": "park",
                            "payload_contains": '"tenant": "acme"',
                        },
                    )
                ).json()
                assert preview["message_count"] == 5  # 3 unique + 2 duplicates
                assert preview["unique_fingerprints"] == 4
                assert preview["duplicate_fingerprints"] == 1

                executed = await client.post(
                    "/api/messages/bulk/execute",
                    json={"batch_id": preview["batch_id"], "confirm": True},
                )
                assert executed.status_code == 200
                summary = executed.json()["summary"]
                assert summary == {
                    "fingerprints_requested": 4,
                    "succeeded": 3,
                    "failed": 0,
                    "skipped_duplicates": 1,
                    "not_found": 0,
                    "not_attempted": 0,
                }

                # 3 parked; the non-matching message and both duplicates remain
                remaining = (await client.get(f"/api/queues/{dlq}/messages")).json()["messages"]
                assert len(remaining) == 3
                parked_queue = await channel.declare_queue(parking, passive=True)
                parked_ids = set()
                for _ in range(3):
                    parked = await parked_queue.get(timeout=5)
                    parked_ids.add(parked.message_id)
                    await parked.ack()
                assert parked_ids == {"bulk-0", "bulk-1", "bulk-2"}

                # token is one-shot
                again = await client.post(
                    "/api/messages/bulk/execute",
                    json={"batch_id": preview["batch_id"], "confirm": True},
                )
                assert again.status_code == 404

                # audit: attempt envelope, one event per fingerprint, closing envelope
                events = (await client.get(f"/api/audit?source_queue={dlq}")).json()["events"]
                envelope = [e for e in events if e["action"] == "bulk_park"]
                per_message = [e for e in events if e["action"] == "park"]
                assert [e["result"] for e in envelope] == ["success", "started"]  # newest first
                assert envelope[0]["metadata"]["succeeded"] == 3
                assert len(per_message) == 4  # 3 success + 1 skipped_duplicate
    finally:
        async with contextlib.AsyncExitStack() as stack:
            stack.push_async_callback(connection.close)
            cleanup = await connection.channel()
            for queue_name in (dlq, parking):
                with contextlib.suppress(Exception):
                    await cleanup.queue_delete(queue_name)


@pytest.mark.asyncio
async def test_quorum_delivery_limits_against_real_broker(tmp_path) -> None:
    """Every preview is a delivery on a quorum queue with a delivery limit — the guard
    must refuse exactly the queues where the broker would drop messages, on 3.x and 4.x."""
    import asyncio

    suffix = uuid.uuid4().hex[:8]
    auth = (_amqp.username or "guest", _amqp.password or "guest")
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as mgmt:
        major = int((await mgmt.get("/api/overview")).json()["rabbitmq_version"].split(".")[0])
    queues = {  # name → (arguments, refused?)
        f"it.classic.{suffix}": ({}, False),
        f"it.q.limit2.{suffix}": ({"x-queue-type": "quorum", "x-delivery-limit": 2}, True),
        # 4.x applies a default limit of 20 that the Management API never shows
        f"it.q.default.{suffix}": ({"x-queue-type": "quorum"}, major >= 4),
        # -1 is unlimited on 4.x — and drops on the first return on 3.x
        f"it.q.unlimited.{suffix}": ({"x-queue-type": "quorum", "x-delivery-limit": -1}, major < 4),
    }
    connection = await aio_pika.connect_robust(AMQP_URL)
    channel = await connection.channel()
    try:
        async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as mgmt:
            for name, (arguments, _refused) in queues.items():
                # over HTTP: aio-pika's table encoder can't send the negative limit
                await mgmt.put(f"/api/queues/%2F/{name}",
                               json={"durable": True, "arguments": arguments})
                await channel.default_exchange.publish(
                    aio_pika.Message(b'{"precious": true}', message_id="p-1"), routing_key=name
                )
            for name in queues:  # the guard fails closed until a queue's first stats land
                for _ in range(60):
                    if "messages" in (await mgmt.get(f"/api/queues/%2F/{name}")).json():
                        break
                    await asyncio.sleep(0.5)

        async def settled_count(name: str, timeout: float = 15.0) -> list[tuple[float, int]]:
            """Quorum enqueues and requeues apply asynchronously (Raft), slower on a cold
            node: poll until the count is 1 or the time is up; a lost message stays at 0."""
            started, seen = asyncio.get_running_loop().time(), []
            while True:
                elapsed = asyncio.get_running_loop().time() - started
                # a fresh channel each time: a robust channel hands back the cached Declare-Ok
                # of its first declare, so a message lost after that would still read as 1
                async with connection.channel() as fresh:
                    n = (await fresh.declare_queue(name, passive=True)).declaration_result
                seen.append((round(elapsed, 1), n.message_count))
                if n.message_count == 1 or elapsed > timeout:
                    return seen
                await asyncio.sleep(0.25)

        # the message must have landed before anything is previewed — otherwise an empty
        # queue later would look like a loss caused by the previews
        for name in queues:
            landed = await settled_count(name)
            assert landed[-1][1] == 1, f"{name}: the published message never landed: {landed}"

        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                for name, (_arguments, refused) in queues.items():
                    seen_sizes = []
                    for _ in range(25):  # past the 4.x default of 20
                        response = await client.get(f"/api/queues/{name}/messages")
                        expected = 409 if refused else 200
                        assert response.status_code == expected, (name, response.text)
                        if not refused:
                            seen_sizes.append(len(response.json()["messages"]))
                    if refused:
                        assert "delivery limit" in response.json()["detail"]
                    else:  # every preview of a browsable queue saw the one message
                        assert set(seen_sizes) == {1}, (name, seen_sizes)
                    timeline = await settled_count(name)
                    assert timeline[-1][1] == 1, f"{name} lost its message: {timeline}"
    finally:
        cleanup = await connection.channel()
        for name in queues:
            with contextlib.suppress(Exception):
                await cleanup.queue_delete(name)
        await connection.close()


@pytest.mark.asyncio
async def test_refused_replays_and_exact_expiration_against_real_broker(tmp_path) -> None:
    """RabbitMQ validates user_id against the publishing connection's user, so a message a
    different broker user published (with its user_id set) can't be replayed by QueueLens's
    user. Nothing may be lost or hidden: a single replay explains itself and changes nothing;
    a bulk replay stops but reports — and audits — what already moved. Replays also keep the
    original expiration to the millisecond."""
    import secrets

    from app.infrastructure.rabbitmq.message_operator import _Replay

    suffix = uuid.uuid4().hex[:8]
    dlq, target = f"it.uid.dlq.{suffix}", f"it.uid.target.{suffix}"
    other = (f"it-uid-{suffix}", secrets.token_urlsafe(12))
    management = httpx.AsyncClient(
        base_url=MANAGEMENT_URL, auth=(_amqp.username or "guest", _amqp.password or "guest")
    )
    (await management.put(f"/api/users/{other[0]}", json={"password": other[1], "tags": ""}))\
        .raise_for_status()
    (await management.put(f"/api/permissions/%2F/{other[0]}",
                          json={"configure": ".*", "write": ".*", "read": ".*"})).raise_for_status()
    connection = await aio_pika.connect_robust(AMQP_URL)
    channel = await connection.channel()

    async def count(queue: str) -> int:
        # a fresh channel: a robust channel hands back its cached Queue — and the stale
        # Declare-Ok of the first declare — for a name it already declared
        async with connection.channel() as fresh:
            declared = await fresh.declare_queue(queue, passive=True)
            return int(declared.declaration_result.message_count or 0)

    try:
        await channel.declare_queue(dlq, durable=True)
        await channel.declare_queue(target, durable=True)
        foreign = await aio_pika.connect(AMQP_URL.replace(
            f"{_amqp.username}:{_amqp.password}@", f"{other[0]}:{other[1]}@"))
        async with foreign, foreign.channel() as foreign_channel:
            await foreign_channel.default_exchange.publish(
                aio_pika.Message(b'{"n": "foreign"}', message_id="foreign", user_id=other[0]),
                routing_key=dlq,
            )
        # 65526 ms is a TTL aio-pika's own Message can't send (it truncates it to 65525)
        for name in ("exact", "second"):
            await channel.default_exchange.publish(
                _Replay(f'{{"n": "{name}"}}'.encode(), expiration_ms="65526", message_id=name),
                routing_key=dlq,
            )

        assert (await count(dlq), await count(target)) == (3, 0)
        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                listed = (await client.get(f"/api/queues/{dlq}/messages")).json()["messages"]
                fp = {m["message_id"]: m["fingerprint"] for m in listed}

                moved = await client.post("/api/messages/replay", json={
                    "source_queue": dlq, "fingerprint": fp["exact"], "mode": "move",
                    "target": {"type": "queue", "queue": target}, "confirm": True})
                assert moved.status_code == 200, moved.text
                copy = await (await channel.declare_queue(target, passive=True)).get(timeout=5)
                assert copy.expiration == 65.526  # "65526" on the wire, not "65525"
                await copy.nack(requeue=True)

                refused = await client.post("/api/messages/replay", json={
                    "source_queue": dlq, "fingerprint": fp["foreign"], "mode": "move",
                    "target": {"type": "queue", "queue": target}, "confirm": True})
                assert refused.status_code == 409
                assert "impersonator" in refused.json()["detail"]
                assert (await count(dlq), await count(target)) == (2, 1)  # nothing changed

                preview = (await client.post("/api/messages/bulk/dry-run", json={
                    "source_queue": dlq, "action": "replay", "mode": "move",
                    "target": {"type": "queue", "queue": target}})).json()
                executed = await client.post(
                    "/api/messages/bulk/execute",
                    json={"batch_id": preview["batch_id"], "confirm": True},
                )
                assert executed.status_code == 200, executed.text  # not a bare 502
                result = executed.json()
                status = {r["fingerprint"]: r["status"] for r in result["results"]}
                assert status[fp["foreign"]] == "failed"
                # the batch runs in fingerprint order: "second" ran before the refusal or not
                assert status[fp["second"]] in {"success", "not_attempted"}
                moved_in_bulk = status[fp["second"]] == "success"
                assert (await count(dlq), await count(target)) == (  # every message accounted for
                    1 if moved_in_bulk else 2, 2 if moved_in_bulk else 1)

                events = (await client.get(f"/api/audit?source_queue={dlq}")).json()["events"]
                audited = {e["message_fingerprint"]: e["result"] for e in events
                           if e["action"] == "replay" and e["metadata"].get("batch_id")}
                assert audited == status  # each message's outcome is on record
    finally:
        async with contextlib.AsyncExitStack() as stack:
            stack.push_async_callback(connection.close)
            stack.push_async_callback(management.aclose)
            cleanup = await connection.channel()
            for queue_name in (dlq, target):
                with contextlib.suppress(Exception):
                    await cleanup.queue_delete(queue_name)
            with contextlib.suppress(Exception):
                await management.delete(f"/api/users/{other[0]}")


@pytest.mark.asyncio
async def test_snapshots_page_deep_queues_and_keep_quorum_order(tmp_path) -> None:
    """#4 against a real broker: a snapshot pages a queue far past the preview window and
    an action reaches a message deep in it; a quorum queue (which puts returned messages at
    the back) is read only whole, so browsing it never reorders it."""
    import asyncio

    suffix = uuid.uuid4().hex[:8]
    classic, quorum, target = (f"it.snap.{k}.{suffix}" for k in ("classic", "quorum", "target"))
    auth = (_amqp.username or "guest", _amqp.password or "guest")
    connection = await aio_pika.connect_robust(AMQP_URL)

    async def ids(queue: str) -> list[str]:
        """Drain on a fresh channel (a robust channel can hand back a stale Declare-Ok)."""
        async with connection.channel() as fresh:
            q = await fresh.declare_queue(queue, passive=True)
            out = []
            while (m := await q.get(no_ack=False, fail=False)) is not None:
                out.append(m.message_id)
                await m.ack()
            return out

    async def ready(queue: str, expected: int) -> int:
        for _ in range(40):  # quorum requeues apply asynchronously
            async with connection.channel() as fresh:
                n = (await fresh.declare_queue(queue, passive=True)).declaration_result
            if n.message_count == expected:
                break
            await asyncio.sleep(0.25)
        return int(n.message_count)

    try:
        async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as mgmt:
            major = int((await mgmt.get("/api/overview")).json()["rabbitmq_version"][0])
            # browsable quorum: unlimited deliveries (-1 on 4.x; no limit at all on 3.x)
            q_args = {"x-queue-type": "quorum", **({"x-delivery-limit": -1} if major >= 4 else {})}
            for name, arguments in ((classic, {}), (quorum, q_args), (target, {})):
                await mgmt.put(f"/api/queues/%2F/{name}",
                               json={"durable": True, "arguments": arguments})
            async with connection.channel() as channel:
                for i in range(250):
                    await channel.default_exchange.publish(
                        aio_pika.Message(f'{{"c": {i}}}'.encode(), message_id=f"c{i}"), classic)
                for i in range(12):
                    await channel.default_exchange.publish(
                        aio_pika.Message(f'{{"q": {i}}}'.encode(), message_id=f"q{i}"), quorum)
            for _ in range(60):  # the guard fails closed until the quorum queue's stats land
                if "messages" in (await mgmt.get(f"/api/queues/%2F/{quorum}")).json():
                    break
                await asyncio.sleep(0.5)
        assert await ready(quorum, 12) == 12

        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                base = f"/api/queues/{classic}/messages"
                first = (await client.get(f"{base}?snapshot=new&limit=50")).json()
                sid = first["snapshot"]["id"]
                assert (first["snapshot"]["scanned"], first["total"]) == (250, 250)
                page5 = (await client.get(f"{base}?snapshot={sid}&offset=200&limit=50")).json()
                assert [m["message_id"] for m in page5["messages"]] == [
                    f"c{i}" for i in range(200, 250)]

                deep = next(m for m in page5["messages"] if m["message_id"] == "c230")
                move = {"source_queue": classic, "fingerprint": deep["fingerprint"],
                        "mode": "move", "target": {"type": "queue", "queue": target},
                        "confirm": True}
                window_only = await client.post("/api/messages/replay", json=move)
                assert window_only.status_code == 409  # 230 is past the 100-message window
                reached = await client.post("/api/messages/replay", json={**move, "snapshot": sid})
                assert reached.status_code == 200, reached.text
                assert (await ready(classic, 249), await ready(target, 1)) == (249, 1)

                qbase = f"/api/queues/{quorum}/messages"
                looks = [[m["message_id"] for m in (await client.get(f"{qbase}?limit=5")).json()
                          ["messages"]] for _ in range(2)]
                assert looks == [[f"q{i}" for i in range(5)]] * 2  # no rotation between looks
                assert await ready(quorum, 12) == 12
                snap = (await client.get(f"{qbase}?snapshot=new")).json()
                q9 = next(m for m in snap["messages"] if m["message_id"] == "q9")
                acted = await client.post("/api/messages/replay", json={
                    **move, "source_queue": quorum, "fingerprint": q9["fingerprint"],
                    "snapshot": snap["snapshot"]["id"]})
                assert acted.status_code == 200, acted.text
                assert await ready(quorum, 11) == 11

                limits = {"values": {"limits": {"max_browse_depth": 5}}}
                await client.put("/api/settings", json=limits)
                too_deep = await client.get(f"{qbase}?limit=5")
                assert too_deep.status_code == 409 and "only whole" in too_deep.json()["detail"]
                shallow = (await client.get(f"{base}?snapshot=new")).json()["snapshot"]
                assert (shallow["scanned"], shallow["stopped"]) == (5, "depth")

        # every look and the action requeued the rest in order: the queue is as it was
        assert await ids(quorum) == [f"q{i}" for i in range(12) if i != 9]
    finally:
        async with connection.channel() as cleanup:
            for name in (classic, quorum, target):
                with contextlib.suppress(Exception):
                    await cleanup.queue_delete(name)
        await connection.close()


@pytest.mark.asyncio
async def test_demo_seed_dead_letters_for_real(tmp_path) -> None:
    """`python -m app.demo` (run once by docker compose) must give the quickstart real
    dead-letters: broker-stamped x-death, repeated deaths counted, and no second batch."""
    from app.demo import QUEUES, seed

    prefix = f"it.demo.{uuid.uuid4().hex[:6]}."
    connection = await aio_pika.connect_robust(AMQP_URL)
    try:
        assert await seed(AMQP_URL, prefix) is True
        assert await seed(AMQP_URL, prefix) is False  # already seeded: nothing added

        async with connection.channel() as channel:
            counts = {}
            for _work, dlq, *_rest in QUEUES:
                declared = await channel.declare_queue(prefix + dlq, passive=True)
                counts[dlq] = declared.declaration_result.message_count
            assert counts == {dlq: n for _w, dlq, _e, _r, n, _p, _a in QUEUES}

            queue = await channel.declare_queue(prefix + "payments.retry.dlq", passive=True)
            seen = []
            for _ in range(30):
                message = await queue.get(timeout=5)
                seen.append(message)
            deaths = {m.message_id: m.headers["x-death"][0]["count"] for m in seen}
            async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=10, auth=(
                    _amqp.username or "guest", _amqp.password or "guest")) as mgmt:
                major = int((await mgmt.get("/api/overview")).json()["rabbitmq_version"][0])
            # 3.x adds to the x-death a republished message carries; 4.x starts it afresh
            expected = (1, 5, 3) if major < 4 else (1, 1, 1)
            assert (deaths["pay-0000"], deaths["pay-0003"], deaths["pay-0004"]) == expected
            assert {m.headers["x-death"][0]["queue"] for m in seen} == {prefix + "payments.retry"}
            assert seen[5].content_encoding == "gzip"
            for message in seen:
                await message.nack(requeue=True)
    finally:
        async with connection.channel() as cleanup:
            for work, dlq, exchange, *_rest in QUEUES:
                for name in (work, dlq):
                    with contextlib.suppress(Exception):
                        await cleanup.queue_delete(prefix + name)
                with contextlib.suppress(Exception):
                    await cleanup.exchange_delete(prefix + exchange)
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("QUEUELENS_TEST_POSTGRES_URL"),
                    reason="needs a throwaway PostgreSQL (QUEUELENS_TEST_POSTGRES_URL)")
async def test_two_replicas_take_turns_scanning_one_queue(tmp_path) -> None:
    """Two replicas on one PostgreSQL scan the same queue at the same moment. A scan holds
    what it reads unacked, so side by side each would see only what the other isn't
    holding; the cross-replica lock makes them take turns, and both see all of it."""
    import asyncio

    from app.infrastructure.persistence.database import Database
    from app.infrastructure.persistence.models import Base

    postgres = os.environ["QUEUELENS_TEST_POSTGRES_URL"]
    database = Database(postgres)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await database.close()

    queue = f"it.replicas.{uuid.uuid4().hex[:8]}"
    auth = (_amqp.username or "guest", _amqp.password or "guest")
    connection = await aio_pika.connect_robust(AMQP_URL)
    try:
        async with connection.channel() as channel:
            await channel.declare_queue(queue, durable=True)
            for i in range(300):
                await channel.default_exchange.publish(
                    aio_pika.Message(f'{{"n": {i}}}'.encode(), message_id=f"m{i}"), queue)
        settings = _settings(tmp_path).model_copy(update={"database_url": postgres})
        apps = [create_app(settings), create_app(settings)]
        async with apps[0].router.lifespan_context(apps[0]), \
                apps[1].router.lifespan_context(apps[1]):
            clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test", timeout=60) for app in apps]
            scans = await asyncio.gather(*(
                client.get(f"/api/queues/{queue}/messages", params={"snapshot": "new"})
                for client in clients))
            for client in clients:
                await client.aclose()
        assert [scan.status_code for scan in scans] == [200, 200]
        assert [scan.json()["snapshot"]["scanned"] for scan in scans] == [300, 300]
    finally:
        await connection.close()
        async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as mgmt:
            await mgmt.delete(f"/api/queues/%2F/{queue}")


@pytest.mark.asyncio
async def test_a_replay_policy_against_real_dead_letters(tmp_path) -> None:
    """Real x-death history from real rejections: backoff reads the broker's timestamps,
    a run skips an origin nobody consumes, replays to it once someone does, and parks a
    message that has died too often."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.application.replay_policies import PolicyRunner

    suffix = uuid.uuid4().hex[:8]
    work, dlq = f"it.policy.work.{suffix}", f"it.policy.work.{suffix}.dlq"
    auth = (_amqp.username or "guest", _amqp.password or "guest")
    connection = await aio_pika.connect_robust(AMQP_URL)
    received: list[str] = []

    async def dead_letter(n: int) -> None:
        async with connection.channel() as channel:
            queue = await channel.declare_queue(work, passive=True)
            await channel.default_exchange.publish(
                aio_pika.Message(f'{{"n": {n}}}'.encode(), message_id=f"p{n}"), work)
            for _ in range(50):
                message = await queue.get(no_ack=False, fail=False)
                if message:
                    await message.reject(requeue=False)  # the broker writes x-death
                    return
                await asyncio.sleep(0.05)

    async def consumers_on(queue: str, want: int) -> None:
        async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as mgmt:
            for _ in range(60):  # the Management API reports consumers a few seconds late
                if (await mgmt.get(f"/api/queues/%2F/{queue}")).json().get("consumers") == want:
                    return
                await asyncio.sleep(0.5)

    try:
        async with connection.channel() as channel:
            await channel.declare_queue(dlq, durable=True)
            await channel.declare_queue(work, durable=True, arguments={
                "x-dead-letter-exchange": "", "x-dead-letter-routing-key": dlq})
        for n in range(3):
            await dead_letter(n)
        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app):
            policy = dict(await app.state.replay_policies.create(
                "admin", name="work", queue=dlq, max_deaths=3, backoff_minutes=5,
                interval_minutes=10, cap=100, enabled=True))
            now = PolicyRunner(app.state)  # the real clock: they died a moment ago
            assert (await now.run(policy, preview=True))["waiting"] == 3
            later = PolicyRunner(app.state, clock=lambda: datetime.now(UTC) + timedelta(hours=2))
            skipped = await later.run(policy)
            assert (skipped["skipped_no_consumers"], skipped["replayed"]) == (3, 0)

            listener = await connection.channel()
            work_queue = await listener.declare_queue(work, passive=True)

            async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
                received.append(message.message_id)
                await message.ack()

            await work_queue.consume(on_message)
            await consumers_on(work, 1)
            replayed = await later.run(policy)
            for _ in range(40):
                if len(received) == 3:
                    break
                await asyncio.sleep(0.1)
            assert replayed["replayed"] == 3 and sorted(received) == ["p0", "p1", "p2"]
            await listener.close()
            await consumers_on(work, 0)

            await dead_letter(9)
            parking = dict(await app.state.replay_policies.update(policy["id"], max_deaths=1))
            parked = await later.run(parking)
            assert parked["parked"] == 1
            rows = await app.state.audit_repository.list(action="run_replay_policy")
            assert rows and rows[0]["username"] == "policy:work"
    finally:
        await connection.close()
        async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as mgmt:
            for queue in (work, dlq, f"{dlq}.parking"):
                await mgmt.delete(f"/api/queues/%2F/{queue}")


@pytest.mark.asyncio
async def test_a_message_that_keeps_failing_is_parked_on_every_broker(tmp_path) -> None:
    """A consumer that rejects every replay: the policy replays the message twice, then
    parks it. RabbitMQ 4.x restarts x-death at 1 for a republished message, so without
    QueueLens' own death count it would look like a first death every time, never parked."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.application.replay_policies import PolicyRunner

    suffix = uuid.uuid4().hex[:8]
    work, dlq = f"it.poison.{suffix}", f"it.poison.{suffix}.dlq"
    auth = (_amqp.username or "guest", _amqp.password or "guest")
    connection = await aio_pika.connect_robust(AMQP_URL)

    async def lands_in_dlq() -> None:
        async with connection.channel() as channel:
            for _ in range(100):
                queue = await channel.declare_queue(dlq, passive=True)
                if queue.declaration_result.message_count == 1:
                    return
                await asyncio.sleep(0.05)
        raise AssertionError("the message never came back to the DLQ")

    try:
        channel = await connection.channel()
        await channel.declare_queue(dlq, durable=True)
        work_queue = await channel.declare_queue(work, durable=True, arguments={
            "x-dead-letter-exchange": "", "x-dead-letter-routing-key": dlq})

        async def poison(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            await message.reject(requeue=False)  # fails every time: the broker dead-letters it

        await work_queue.consume(poison)
        await channel.default_exchange.publish(aio_pika.Message(b'{"n": 1}'), work)
        await lands_in_dlq()
        async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as mgmt:
            for _ in range(60):  # the Management API reports consumers a few seconds late
                if (await mgmt.get(f"/api/queues/%2F/{work}")).json().get("consumers") == 1:
                    break
                await asyncio.sleep(0.5)

        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app):
            policy = dict(await app.state.replay_policies.create(
                "admin", name="poison", queue=dlq, max_deaths=3, backoff_minutes=1,
                interval_minutes=10, cap=100, enabled=True))
            runs = []
            for hours in (1, 2, 3):  # always past the backoff
                runner = PolicyRunner(
                    app.state, clock=lambda h=hours: datetime.now(UTC) + timedelta(hours=h))
                result = await runner.run(policy)
                runs.append((result["replayed"], result["parked"]))
                if result["replayed"]:
                    await lands_in_dlq()
            assert runs == [(1, 0), (1, 0), (0, 1)]
            parked = await app.state.message_service.snapshot(f"{dlq}.parking", 10)
            assert [record.headers.get("x-queuelens-deaths") for record in parked.records] == [2]
    finally:
        await connection.close()
        async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as mgmt:
            for queue in (work, dlq, f"{dlq}.parking"):
                await mgmt.delete(f"/api/queues/%2F/{queue}")
