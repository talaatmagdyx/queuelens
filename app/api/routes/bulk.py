from datetime import UTC, datetime
from typing import Literal, cast

from aiormq.exceptions import ChannelNotFoundEntity
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.actions import TargetRequest, _custom_headers
from app.api.routes.messages import effective_limit, scan_depth
from app.api.scope import broker, broker_scope
from app.application.bulk_runs import execute_audited
from app.application.bulk_service import BulkActionService, UnknownBulkBatch
from app.application.queue_service import UnsafeToBrowse
from app.auth.basic import CurrentUser, require_operator
from app.domain.models import AuditEntry
from app.infrastructure.rabbitmq.message_operator import REFUSALS, error_text, refusal_text

router = APIRouter(
    prefix="/api/messages/bulk", tags=["bulk"], dependencies=[Depends(broker_scope)]
)


class SnapshotMatch(BaseModel):
    """The filters of a snapshot's pages: every message of the snapshot they match."""

    contains: str | None = Field(default=None, max_length=256)
    payload_format: Literal["json", "text", "base64"] | None = None
    min_deaths: int | None = Field(default=None, ge=1)


class BulkDryRunRequest(BaseModel):
    source_queue: str = Field(min_length=1)
    action: Literal["replay", "park", "delete"]
    mode: Literal["copy", "move"] = "copy"
    target: TargetRequest | None = None
    payload_contains: str | None = None
    fingerprints: list[str] | None = Field(default=None, max_length=1000)
    snapshot: str | None = Field(default=None, max_length=64)  # where they were picked
    match: SnapshotMatch | None = None  # instead of fingerprints: all that match in `snapshot`


class BulkExecuteRequest(BaseModel):
    batch_id: str = Field(min_length=8)
    confirm: bool = False


def _service(request: Request) -> BulkActionService:
    return cast(BulkActionService, broker(request).bulk_service)


@router.post("/dry-run")
async def dry_run(
    request: Request,
    body: BulkDryRunRequest,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, object]:
    if body.action == "delete" and not user.is_admin:
        raise HTTPException(status_code=403, detail="Deleting messages requires the Admin role")
    username = user.username
    max_bulk = await effective_limit(request, "max_bulk_size")
    fingerprints = body.fingerprints
    if body.match is not None:  # "select all matching": the snapshot picks them
        if fingerprints is not None or not body.snapshot:
            raise HTTPException(
                status_code=400, detail="`match` picks from a snapshot: send it with "
                "`snapshot` and without `fingerprints`",
            )
        found = request.app.state.snapshots.get(
            body.snapshot, request.state.scope, body.source_queue
        )
        if found is None:
            raise HTTPException(
                status_code=404, detail="Snapshot expired or unknown — scan the queue again"
            )
        records = found.matching(**body.match.model_dump())
        fingerprints = list(dict.fromkeys(record.fingerprint for record in records))
    if fingerprints is not None and len(fingerprints) > max_bulk:
        raise HTTPException(
            status_code=400,
            detail=f"{len(fingerprints)} messages selected; one bulk run acts on at most "
            f"{max_bulk}",
        )
    scan_limit = None
    if fingerprints and body.snapshot:  # reach the deepest selected message
        scan_limit = max(
            max_bulk,
            await scan_depth(request, body.source_queue, fingerprints, body.snapshot),
        )
    try:
        return await _service(request).dry_run(
            source_queue=body.source_queue,
            action=body.action,
            mode=body.mode,
            target=body.target.to_domain() if body.target else None,
            payload_contains=body.payload_contains,
            selected_fingerprints=(
                frozenset(fingerprints) if fingerprints is not None else None
            ),
            max_bulk=max_bulk,
            scan_limit=scan_limit,
            depth=await effective_limit(request, "max_browse_depth"),
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
                error_message=error_text(error),
                metadata={"stage": "dry_run", "mode": body.mode},
            )
        )
        if isinstance(error, UnsafeToBrowse):
            raise HTTPException(status_code=409, detail=str(error)) from error
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
    pending_check = await _service(request).peek_batch(body.batch_id)
    if pending_check and pending_check.action == "delete" and not user.is_admin:
        raise HTTPException(status_code=403, detail="Deleting messages requires the Admin role")
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Bulk execution confirmation is required")
    try:
        _batch, outcome = await execute_audited(
            _service(request), request.app.state.audit_repository, body.batch_id, username,
            await _custom_headers(request),
        )
    except UnknownBulkBatch as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except UnsafeToBrowse as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ChannelNotFoundEntity as error:
        raise HTTPException(
            status_code=404,
            detail="Queue not found; check the source queue and replay target",
        ) from error
    except REFUSALS as error:
        # only raised before the first message is touched (target checks); a refusal
        # mid-batch comes back as per-message results instead
        raise HTTPException(
            status_code=409, detail=f"{refusal_text(error)}. Nothing was moved."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail="Bulk operation failed") from error
    return outcome
