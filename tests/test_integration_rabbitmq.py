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

                # Regression: the HTML detail page renders datetime-bearing x-death.
                detail = await client.get(f"/messages/{dlq}/{by_id['it-0']}")
                assert detail.status_code == 200
                assert "rejected" in detail.text

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
