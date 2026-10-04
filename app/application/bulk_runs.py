"""Executing a bulk dry-run batch with its full audit trail, for a person (the bulk API)
or for a replay policy: the attempt is recorded before the broker is touched, then each
message's outcome, then the batch's."""

import time
from datetime import UTC, datetime
from typing import Any, cast

from app.application.action_service import provenance_headers
from app.application.bulk_service import BulkActionService, BulkBatch, UnknownBulkBatch
from app.domain.models import AuditEntry
from app.infrastructure.rabbitmq.message_operator import error_text
from app.observability.metrics import ACTIONS, OPERATION_SECONDS


def _target_fields(batch: BulkBatch | None) -> dict[str, Any]:
    target = batch.target if batch else None
    return {
        "target_type": target.type if target else None,
        "target_queue": target.queue if target else None,
        "target_exchange": target.exchange if target else None,
        "target_routing_key": target.routing_key if target else None,
    }


async def execute_audited(
    service: BulkActionService,
    audit: Any,  # AuditRepository
    batch_id: str,
    username: str,
    headers: dict[str, object],
) -> tuple[BulkBatch, dict[str, object]]:
    """Run a batch; raises what BulkActionService.execute raises (after auditing it)."""
    pending = await service.peek_batch(batch_id)
    replay_headers: dict[str, Any] = dict(headers)
    if pending and pending.operator_action != "delete":
        replay_headers.update(
            provenance_headers(pending.operator_action, pending.source_queue, username)
        )
    if pending:
        # the attempt is on record before the broker is touched: no audit, no action
        await audit.record(AuditEntry(
            username=username, action=f"bulk_{pending.action}", timestamp=datetime.now(UTC),
            source_queue=pending.source_queue, result="started", **_target_fields(pending),
            metadata={"batch_id": batch_id, "mode": pending.operator_action,
                      "fingerprints": len(pending.fingerprints)},
        ))
    started_at = time.perf_counter()
    try:
        batch, outcome = await service.execute(batch_id, replay_headers=replay_headers)
    except UnknownBulkBatch:
        raise
    except Exception as error:
        await audit.record(AuditEntry(
            username=username, action=f"bulk_{pending.action}" if pending else "bulk",
            timestamp=datetime.now(UTC), source_queue=pending.source_queue if pending else None,
            result="failed", error_message=error_text(error), **_target_fields(pending),
            metadata={"batch_id": batch_id,
                      "mode": pending.operator_action if pending else None},
        ))
        raise

    summary = cast(dict[str, int], outcome["summary"])
    bulk_action = f"bulk_{batch.action}"
    elapsed = time.perf_counter() - started_at
    OPERATION_SECONDS.labels(action=bulk_action).observe(elapsed)
    ACTIONS.labels(action=bulk_action,
                   result="success" if summary["failed"] == 0 else "partial").inc()
    for label, count in (
        ("success", summary["succeeded"]),
        ("failed", summary["failed"]),
        ("skipped_duplicate", summary["skipped_duplicates"]),
        ("not_found", summary["not_found"]),
        ("not_attempted", summary.get("not_attempted", 0)),
    ):
        if count:
            ACTIONS.labels(action=batch.action, result=label).inc(count)
    for result in cast(list[dict[str, Any]], outcome["results"]):
        await audit.record(AuditEntry(
            username=username, action=batch.action, timestamp=datetime.now(UTC),
            source_queue=batch.source_queue, message_fingerprint=str(result["fingerprint"]),
            result="success" if result["status"] == "success" else str(result["status"]),
            error_message=cast(str | None, result.get("error")),
            metadata={"batch_id": batch_id},
        ))
    await audit.record(AuditEntry(
        username=username, action=bulk_action, timestamp=datetime.now(UTC),
        source_queue=batch.source_queue, **_target_fields(batch),
        result="success" if summary["failed"] == 0 else "partial",
        metadata={"batch_id": batch_id, "duration_ms": round(elapsed * 1000),
                  "mode": batch.operator_action, **summary},
    ))
    return batch, outcome
