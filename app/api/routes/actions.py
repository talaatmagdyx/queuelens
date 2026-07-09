from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.application.action_service import ActionService
from app.auth.basic import get_current_username
from app.domain.models import AuditEntry, ReplayTarget

router = APIRouter(prefix="/api/messages", tags=["actions"])


class TargetRequest(BaseModel):
    type: Literal["queue", "exchange"]
    queue: str | None = None
    exchange: str | None = None
    routing_key: str | None = None

    def to_domain(self) -> ReplayTarget:
        return ReplayTarget(
            type=self.type,
            queue=self.queue,
            exchange=self.exchange,
            routing_key=self.routing_key,
        )


class ReplayRequest(BaseModel):
    source_queue: str = Field(min_length=1)
    fingerprint: str = Field(min_length=8)
    mode: Literal["copy", "move"] = "copy"
    target: TargetRequest | None = None
    confirm: bool = False


class MessageActionRequest(BaseModel):
    source_queue: str = Field(min_length=1)
    fingerprint: str = Field(min_length=8)
    confirm: bool = False


def _service(request: Request) -> ActionService:
    return cast(ActionService, request.app.state.action_service)


async def _run_action(
    request: Request,
    username: str,
    action: str,
    source_queue: str,
    fingerprint: str,
    operation: Callable[[], Awaitable[dict[str, object]]],
) -> dict[str, object]:
    audit = request.app.state.audit_repository
    await audit.record(
        AuditEntry(
            username=username,
            action=action,
            timestamp=datetime.now(UTC),
            source_queue=source_queue,
            message_fingerprint=fingerprint,
            result="started",
        )
    )
    try:
        result = await operation()
    except Exception as error:
        await audit.record(
            AuditEntry(
                username=username,
                action=action,
                timestamp=datetime.now(UTC),
                source_queue=source_queue,
                message_fingerprint=fingerprint,
                result="failed",
                error_message=str(error),
            )
        )
        if isinstance(error, LookupError):
            raise HTTPException(
                status_code=409,
                detail="Message was not found uniquely; refresh and try again",
            ) from error
        if isinstance(error, ValueError):
            raise HTTPException(status_code=400, detail=str(error)) from error
        raise HTTPException(status_code=502, detail="Message operation failed") from error
    await audit.record(
        AuditEntry(
            username=username,
            action=action,
            timestamp=datetime.now(UTC),
            source_queue=source_queue,
            message_fingerprint=fingerprint,
            result="success",
        )
    )
    return result


@router.post("/replay")
async def replay(
    request: Request,
    body: ReplayRequest,
    username: str = Depends(get_current_username),
) -> dict[str, object]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Replay confirmation is required")
    return await _run_action(
        request,
        username,
        "replay",
        body.source_queue,
        body.fingerprint,
        lambda: _service(request).replay(
            source_queue=body.source_queue,
            fingerprint=body.fingerprint,
            mode=body.mode,
            target=body.target.to_domain() if body.target else None,
            username=username,
        ),
    )


@router.post("/park")
async def park(
    request: Request,
    body: MessageActionRequest,
    username: str = Depends(get_current_username),
) -> dict[str, object]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Park confirmation is required")
    return await _run_action(
        request,
        username,
        "park",
        body.source_queue,
        body.fingerprint,
        lambda: _service(request).park(
            source_queue=body.source_queue,
            fingerprint=body.fingerprint,
        ),
    )


@router.post("/delete")
async def delete(
    request: Request,
    body: MessageActionRequest,
    username: str = Depends(get_current_username),
) -> dict[str, object]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Delete confirmation is required")
    return await _run_action(
        request,
        username,
        "delete",
        body.source_queue,
        body.fingerprint,
        lambda: _service(request).delete(
            source_queue=body.source_queue,
            fingerprint=body.fingerprint,
        ),
    )
