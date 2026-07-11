from typing import Any

import httpx
import pytest
from aio_pika.exceptions import DeliveryError

from app.application.bulk_service import BulkActionService, UnknownBulkBatch
from app.config import Settings
from app.domain.models import MessageRecord, ReplayTarget
from app.infrastructure.rabbitmq.message_browser import MessageBrowser
from app.infrastructure.rabbitmq.message_operator import MessageOperator
from app.main import create_app
from tests.test_actions import ActionChannel, ActionConnection, ActionMessage, ChannelContext


class UnroutableExchange:
    def __init__(self, fail_routing_keys: set[str]) -> None:
        self.fail_routing_keys = fail_routing_keys
        self.published: list[tuple[object, str]] = []

    async def publish(self, message: object, routing_key: str) -> None:
        if routing_key in self.fail_routing_keys:
            raise DeliveryError(None, None)  # type: ignore[arg-type]
        self.published.append((message, routing_key))


def _messages(count: int, *, dup_body: bytes | None = None) -> list[ActionMessage]:
    out = []
    for index in range(count):
        message = ActionMessage()
        message.message_id = f"bulk-{index}"
        out.append(message)
    if dup_body is not None:
        for _ in range(2):
            dup = ActionMessage()
            dup.message_id = "dup"
            dup.body = dup_body
            out.append(dup)
    return out


@pytest.mark.asyncio
async def test_bulk_park_acts_per_message_and_skips_duplicates() -> None:
    messages = _messages(2, dup_body=b'{"dup": true}')
    fingerprints = frozenset(
        MessageBrowser._to_record("orders.dlq", m).fingerprint for m in messages
    )
    from tests.test_actions import FakeExchange

    exchange = FakeExchange()
    channel = ActionChannel(list(messages), exchange)
    operator = MessageOperator(ActionConnection(ChannelContext(channel)))  # type: ignore[arg-type]

    results = await operator.operate_bulk(
        source_queue="orders.dlq",
        fingerprints=fingerprints,
        action="park",
        target=ReplayTarget(type="queue", queue="orders.dlq.parking"),
        max_scan=10,
    )

    by_status = {str(r["status"]) for r in results}
    assert by_status == {"success", "skipped_duplicate"}
    assert sum(1 for r in results if r["status"] == "success") == 2
    assert sum(1 for r in results if r["status"] == "skipped_duplicate") == 1
    # the two unique messages were acked, both duplicates requeued
    assert [m.acked for m in messages[:2]] == [True, True]
    assert [m.nacked for m in messages[2:]] == [True, True]
    # parking queue was created before publishing
    assert ("orders.dlq.parking", False, True) in channel.declared
    assert len(exchange.published) == 2


@pytest.mark.asyncio
async def test_bulk_unroutable_publish_fails_that_message_and_continues() -> None:
    good, bad = _messages(2)
    exchange = UnroutableExchange(fail_routing_keys=set())
    channel = ActionChannel([good, bad], exchange)
    operator = MessageOperator(ActionConnection(ChannelContext(channel)))  # type: ignore[arg-type]
    good_fp = MessageBrowser._to_record("orders.dlq", good).fingerprint
    bad_fp = MessageBrowser._to_record("orders.dlq", bad).fingerprint

    async def publish(message: Any, routing_key: str) -> None:
        headers = message.headers
        if headers.get("x-queuelens-original-fingerprint") == bad_fp:
            raise DeliveryError(None, None)  # type: ignore[arg-type]
        exchange.published.append((message, routing_key))

    exchange.publish = publish  # type: ignore[method-assign]

    results = await operator.operate_bulk(
        source_queue="orders.dlq",
        fingerprints=frozenset({good_fp, bad_fp}),
        action="move",
        target=ReplayTarget(type="queue", queue="orders.retry"),
        max_scan=10,
    )

    statuses = {str(r["fingerprint"]): str(r["status"]) for r in results}
    assert statuses[good_fp] == "success"
    assert statuses[bad_fp] == "failed"
    assert good.acked is True
    assert bad.acked is False
    assert bad.nacked is True  # requeued, not lost


@pytest.mark.asyncio
async def test_dry_run_filters_and_execute_is_one_shot() -> None:
    def record(message_id: str, body: bytes) -> MessageRecord:
        return MessageRecord(
            fingerprint=message_id * 8,
            source_queue="orders.dlq",
            body=body,
            payload={},
            payload_format="json",
            payload_size=len(body),
            content_type="application/json",
            message_id=message_id,
            correlation_id=None,
            timestamp=None,
            exchange="",
            routing_key="orders.dlq",
            headers={},
            properties={},
            redelivered=False,
        )

    class FakeBrowser:
        async def list_messages(self, _queue: str, _limit: int) -> list[MessageRecord]:
            return [
                record("aaaaaaaa", b'{"customer": "acme"}'),
                record("bbbbbbbb", b'{"customer": "globex"}'),
            ]

    class FakeOperator:
        def __init__(self) -> None:
            self.calls: list[frozenset[str]] = []

        async def operate_bulk(self, **kwargs: Any) -> list[dict[str, object]]:
            self.calls.append(kwargs["fingerprints"])
            return [{"fingerprint": fp, "status": "success"} for fp in kwargs["fingerprints"]]

    operator = FakeOperator()
    service = BulkActionService(Settings(), FakeBrowser(), operator)  # type: ignore[arg-type]

    preview = await service.dry_run(
        source_queue="orders.dlq", action="delete", payload_contains="acme"
    )
    assert preview["message_count"] == 1
    assert preview["unique_fingerprints"] == 1

    batch_id = str(preview["batch_id"])
    _batch, outcome = await service.execute(batch_id)
    assert outcome["summary"] == {
        "fingerprints_requested": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped_duplicates": 0,
        "not_found": 0,
    }
    assert operator.calls == [frozenset({"aaaaaaaa" * 8})]

    with pytest.raises(UnknownBulkBatch):  # one-shot token
        await service.execute(batch_id)


@pytest.mark.asyncio
async def test_bulk_replay_requires_target_and_park_derives_parking_queue() -> None:
    class EmptyBrowser:
        async def list_messages(self, _queue: str, _limit: int) -> list[MessageRecord]:
            return []

    service = BulkActionService(Settings(), EmptyBrowser(), object())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="No replay target configured"):
        await service.dry_run(source_queue="orders.dlq", action="replay")

    preview = await service.dry_run(source_queue="orders.dlq", action="park")
    assert preview["target"] == {
        "type": "queue",
        "queue": "orders.dlq.parking",
        "exchange": None,
        "routing_key": None,
    }
    assert preview["message_count"] == 0


@pytest.mark.asyncio
async def test_bulk_routes_confirmation_expiry_and_audit(tmp_path) -> None:
    settings = Settings(
        auth_enabled=False,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/bulk.db",
    )
    app = create_app(settings)
    await app.state.database.start()

    class FakeBulkService:
        async def dry_run(self, **_kwargs: object) -> dict[str, object]:
            return {"batch_id": "batch-1234", "message_count": 2}

        async def peek_batch(self, _batch_id: str) -> None:
            return None

        async def execute(self, batch_id: str, **_kwargs: object):
            if batch_id == "expired-1234":
                raise UnknownBulkBatch("Unknown or expired dry-run batch")
            from app.application.bulk_service import BulkBatch

            batch = BulkBatch(
                id=batch_id,
                source_queue="orders.dlq",
                action="delete",
                operator_action="delete",
                target=None,
                fingerprints=frozenset({"a" * 64, "b" * 64}),
                message_count=2,
                duplicate_fingerprints=0,
            )
            return batch, {
                "batch_id": batch_id,
                "action": "delete",
                "source_queue": "orders.dlq",
                "target": None,
                "summary": {
                    "fingerprints_requested": 2,
                    "succeeded": 1,
                    "failed": 1,
                    "skipped_duplicates": 0,
                    "not_found": 0,
                },
                "results": [
                    {"fingerprint": "a" * 64, "status": "success"},
                    {"fingerprint": "b" * 64, "status": "failed", "error": "boom"},
                ],
            }

    app.state.bulk_service = FakeBulkService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        no_confirm = await client.post(
            "/api/messages/bulk/execute", json={"batch_id": "batch-1234"}
        )
        expired = await client.post(
            "/api/messages/bulk/execute", json={"batch_id": "expired-1234", "confirm": True}
        )
        executed = await client.post(
            "/api/messages/bulk/execute", json={"batch_id": "batch-1234", "confirm": True}
        )

    from app.infrastructure.persistence.audit_repository import AuditRepository

    events = await AuditRepository(app.state.database).list(source_queue="orders.dlq")
    await app.state.database.close()

    assert no_confirm.status_code == 400
    assert expired.status_code == 404
    assert executed.status_code == 200
    assert executed.json()["summary"]["succeeded"] == 1
    actions = sorted(event["action"] for event in events)
    # one per-message event per fingerprint plus the batch envelope
    assert actions == ["bulk_delete", "delete", "delete"]
    envelope = next(event for event in events if event["action"] == "bulk_delete")
    assert envelope["result"] == "partial"
    assert envelope["metadata"]["succeeded"] == 1


@pytest.mark.asyncio
async def test_dry_run_with_explicit_selection() -> None:
    def record(message_id: str) -> MessageRecord:
        return MessageRecord(
            fingerprint=message_id * 8,
            source_queue="orders.dlq",
            body=b"{}",
            payload={},
            payload_format="json",
            payload_size=2,
            content_type="application/json",
            message_id=message_id,
            correlation_id=None,
            timestamp=None,
            exchange="",
            routing_key="orders.dlq",
            headers={},
            properties={},
            redelivered=False,
        )

    class FakeBrowser:
        async def list_messages(self, _queue: str, _limit: int) -> list[MessageRecord]:
            return [record("aaaaaaaa"), record("bbbbbbbb"), record("cccccccc")]

    service = BulkActionService(Settings(), FakeBrowser(), object())  # type: ignore[arg-type]

    preview = await service.dry_run(
        source_queue="orders.dlq",
        action="delete",
        selected_fingerprints=frozenset({"aaaaaaaa" * 8, "cccccccc" * 8, "gone" * 16}),
    )

    assert preview["message_count"] == 2  # only the selected-and-present messages
    assert preview["unique_fingerprints"] == 2
    assert preview["selected_not_seen"] == 1  # the vanished selection is reported


@pytest.mark.asyncio
async def test_dry_run_failure_is_audited_and_maps_missing_queue_to_404(tmp_path) -> None:
    from aiormq.exceptions import ChannelNotFoundEntity

    settings = Settings(
        auth_enabled=False,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/dr.db",
    )
    app = create_app(settings)
    await app.state.database.start()

    class FakeBulkService:
        async def dry_run(self, **_kwargs: object) -> dict[str, object]:
            raise ChannelNotFoundEntity("NOT_FOUND - no queue 'ghost.dlq' in vhost '/'")

    app.state.bulk_service = FakeBulkService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/messages/bulk/dry-run",
            json={"source_queue": "ghost.dlq", "action": "replay", "fingerprints": ["f" * 64]},
        )
        audit = await client.get("/api/audit?limit=5")
    await app.state.database.close()

    assert response.status_code == 404
    assert "Queue not found" in response.json()["detail"]
    events = audit.json()["events"]
    assert events[0]["action"] == "bulk_replay"
    assert events[0]["result"] == "failed"
    assert events[0]["source_queue"] == "ghost.dlq"
    assert "no queue 'ghost.dlq'" in events[0]["error_message"]
