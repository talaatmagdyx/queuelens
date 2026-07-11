import time
from datetime import UTC, datetime
from typing import Any, Literal, cast

from aiormq.exceptions import ChannelNotFoundEntity
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.actions import TargetRequest
from app.application.bulk_service import BulkActionService, UnknownBulkBatch
from app.auth.basic import CurrentUser, require_operator
from app.domain.models import AuditEntry
from app.observability.metrics import ACTIONS, OPERATION_SECONDS

router = APIRouter(prefix="/api/messages/bulk", tags=["bulk"])


class BulkDryRunRequest(BaseModel):
    source_queue: str = Field(min_length=1)
    action: Literal["replay", "park", "delete"]
    mode: Literal["copy", "move"] = "copy"
    target: TargetRequest | None = None
    payload_contains: str | None = None
    fingerprints: list[str] | None = Field(default=None, max_length=1000)


class BulkExecuteRequest(BaseModel):
    batch_id: str = Field(min_length=8)
    confirm: bool = False


def _service(request: Request) -> BulkActionService:
    return cast(BulkActionService, request.app.state.bulk_service)


@router.post("/dry-run")
async def dry_run(
    request: Request,
    body: BulkDryRunRequest,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, object]:
    if body.action == "delete" and not user.is_admin:
        raise HTTPException(status_code=403, detail="Deleting messages requires the Admin role")
    username = user.username
    stored = await request.app.state.settings_store.get_safe("limits", {}) or {}
    max_bulk = stored.get("max_bulk_size")
    try:
        return await _service(request).dry_run(
            source_queue=body.source_queue,
            action=body.action,
            mode=body.mode,
            target=body.target.to_domain() if body.target else None,
            payload_contains=body.payload_contains,
            selected_fingerprints=(
                frozenset(body.fingerprints) if body.fingerprints is not None else None
            ),
            max_bulk=min(int(max_bulk), 1000) if max_bulk else None,
        )
    except Exception as error:
        # Dry-run failures are audited too — a rejected bulk attempt is still an attempt.
        await request.app.state.audit_repository.record(
            AuditEntry(
                username=username,
                action=f"bulk_{body.action}",
                timestamp=datetime.now(UTC),
                source_queue=body.source_queue,
                target_type=body.target.type if body.target else None,
                target_queue=body.target.queue if body.target else None,
                target_exchange=body.target.exchange if body.target else None,
                target_routing_key=body.target.routing_key if body.target else None,
                result="failed",
                error_message=str(error),
                metadata={"stage": "dry_run", "mode": body.mode},
            )
        )
        if isinstance(error, ChannelNotFoundEntity):
            raise HTTPException(
                status_code=404,
                detail="Queue not found; check the source queue and replay target",
            ) from error
        if isinstance(error, ValueError):
            raise HTTPException(status_code=400, detail=str(error)) from error
        raise HTTPException(status_code=502, detail="Bulk dry-run failed") from error


@router.post("/execute")
async def execute(
    request: Request,
    body: BulkExecuteRequest,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, object]:
    username = user.username
    pending_check = request.app.state.bulk_service.peek(body.batch_id)
    if pending_check and pending_check.action == "delete" and not user.is_admin:
        raise HTTPException(status_code=403, detail="Deleting messages requires the Admin role")
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Bulk execution confirmation is required")
    audit = request.app.state.audit_repository
    service = _service(request)
    replay_headers: dict[str, Any] = {
        "x-queuelens-replayed": True,
        "x-queuelens-replayed-at": datetime.now(UTC).isoformat(),
        "x-queuelens-replayed-by": username,
    }
    started_at = time.perf_counter()
    pending = service.peek(body.batch_id)  # batch context for failure audits
    try:
        batch, outcome = await service.execute(body.batch_id, replay_headers=replay_headers)
    except UnknownBulkBatch as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        await audit.record(
            AuditEntry(
                username=username,
                action=f"bulk_{pending.action}" if pending else "bulk",
                timestamp=datetime.now(UTC),
                source_queue=pending.source_queue if pending else None,
                target_type=pending.target.type if pending and pending.target else None,
                target_queue=pending.target.queue if pending and pending.target else None,
                target_exchange=pending.target.exchange if pending and pending.target else None,
                target_routing_key=(
                    pending.target.routing_key if pending and pending.target else None
                ),
                result="failed",
                error_message=str(error),
                metadata={
                    "batch_id": body.batch_id,
                    "mode": pending.operator_action if pending else None,
                },
            )
        )
        if isinstance(error, ChannelNotFoundEntity):
            raise HTTPException(
                status_code=404,
                detail="Queue not found; check the source queue and replay target",
            ) from error
        if isinstance(error, ValueError):
            raise HTTPException(status_code=400, detail=str(error)) from error
        raise HTTPException(status_code=502, detail="Bulk operation failed") from error

    summary = cast(dict[str, int], outcome["summary"])
    bulk_action = f"bulk_{batch.action}"
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    OPERATION_SECONDS.labels(action=bulk_action).observe(time.perf_counter() - started_at)
    envelope_result = "success" if summary["failed"] == 0 else "partial"
    ACTIONS.labels(action=bulk_action, result=envelope_result).inc()
    for label, count in (
        ("success", summary["succeeded"]),
        ("failed", summary["failed"]),
        ("skipped_duplicate", summary["skipped_duplicates"]),
        ("not_found", summary["not_found"]),
    ):
        if count:
            ACTIONS.labels(action=batch.action, result=label).inc(count)
    for result in cast(list[dict[str, Any]], outcome["results"]):
        await audit.record(
            AuditEntry(
                username=username,
                action=batch.action,
                timestamp=datetime.now(UTC),
                source_queue=batch.source_queue,
                message_fingerprint=str(result["fingerprint"]),
                result="success" if result["status"] == "success" else str(result["status"]),
                error_message=cast(str | None, result.get("error")),
                metadata={"batch_id": body.batch_id},
            )
        )
    await audit.record(
        AuditEntry(
            username=username,
            action=f"bulk_{batch.action}",
            timestamp=datetime.now(UTC),
            source_queue=batch.source_queue,
            target_type=batch.target.type if batch.target else None,
            target_queue=batch.target.queue if batch.target else None,
            target_exchange=batch.target.exchange if batch.target else None,
            target_routing_key=batch.target.routing_key if batch.target else None,
            result="success" if summary["failed"] == 0 else "partial",
            metadata={
                "batch_id": body.batch_id,
                "duration_ms": elapsed_ms,
                "mode": batch.operator_action,
                **summary,
            },
        )
    )
    return outcome
