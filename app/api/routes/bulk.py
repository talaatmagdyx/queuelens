from datetime import UTC, datetime
from typing import Any, Literal, cast

from aiormq.exceptions import ChannelNotFoundEntity
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.actions import TargetRequest
from app.application.bulk_service import BulkActionService, UnknownBulkBatch
from app.auth.basic import get_current_username
from app.domain.models import AuditEntry

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
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
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
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/execute")
async def execute(
    request: Request,
    body: BulkExecuteRequest,
    username: str = Depends(get_current_username),
) -> dict[str, object]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Bulk execution confirmation is required")
    audit = request.app.state.audit_repository
    service = _service(request)
    replay_headers: dict[str, Any] = {
        "x-queuelens-replayed": True,
        "x-queuelens-replayed-at": datetime.now(UTC).isoformat(),
        "x-queuelens-replayed-by": username,
    }
    try:
        batch, outcome = await service.execute(body.batch_id, replay_headers=replay_headers)
    except UnknownBulkBatch as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        await audit.record(
            AuditEntry(
                username=username,
                action="bulk",
                timestamp=datetime.now(UTC),
                result="failed",
                error_message=str(error),
                metadata={"batch_id": body.batch_id},
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
            metadata={"batch_id": body.batch_id, **summary},
        )
    )
    return outcome
