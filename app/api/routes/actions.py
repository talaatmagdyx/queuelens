import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

from aio_pika.exceptions import DeliveryError
from aiormq.exceptions import ChannelNotFoundEntity
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.application.action_service import ActionService
from app.auth.basic import get_current_username
from app.domain.models import AuditEntry, ReplayTarget
from app.observability.metrics import ACTIONS, OPERATION_SECONDS

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
    annotate: bool = True  # stamp x-queuelens-* provenance headers


class MessageActionRequest(BaseModel):
    source_queue: str = Field(min_length=1)
    fingerprint: str = Field(min_length=8)
    confirm: bool = False


async def _custom_headers(request: Request) -> dict[str, object]:
    """Admin-configured headers stamped on every message QueueLens publishes."""
    stored = await request.app.state.settings_store.get_safe("custom_headers", []) or []
    return {
        str(item["key"]): str(item["value"])
        for item in stored
        if isinstance(item, dict) and item.get("key")
    }


def _service(request: Request) -> ActionService:
    return cast(ActionService, request.app.state.action_service)


async def _run_action(
    request: Request,
    username: str,
    action: str,
    source_queue: str,
    fingerprint: str,
    operation: Callable[[], Awaitable[dict[str, object]]],
    target: ReplayTarget | None = None,
    mode: str | None = None,
) -> dict[str, object]:
    audit = request.app.state.audit_repository
    target_fields: dict[str, str | None] = {
        "target_type": target.type if target else None,
        "target_queue": target.queue if target else None,
        "target_exchange": target.exchange if target else None,
        "target_routing_key": target.routing_key if target else None,
    }
    mode_meta: dict[str, object] = {"mode": mode} if mode else {}
    await audit.record(
        AuditEntry(
            username=username,
            action=action,
            timestamp=datetime.now(UTC),
            source_queue=source_queue,
            message_fingerprint=fingerprint,
            result="started",
            metadata=dict(mode_meta),
            **target_fields,
        )
    )
    started_at = time.perf_counter()
    try:
        result = await operation()
    except Exception as error:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        OPERATION_SECONDS.labels(action=action).observe(elapsed_ms / 1000)
        ACTIONS.labels(action=action, result="failed").inc()
        await audit.record(
            AuditEntry(
                username=username,
                action=action,
                timestamp=datetime.now(UTC),
                source_queue=source_queue,
                message_fingerprint=fingerprint,
                result="failed",
                error_message=str(error),
                metadata={"duration_ms": elapsed_ms, **mode_meta},
                **target_fields,
            )
        )
        if isinstance(error, LookupError):
            raise HTTPException(
                status_code=409,
                detail="Message was not found uniquely; refresh and try again",
            ) from error
        if isinstance(error, ChannelNotFoundEntity):
            raise HTTPException(
                status_code=404,
                detail="Queue not found; check the source queue and replay target",
            ) from error
        if isinstance(error, DeliveryError):
            raise HTTPException(
                status_code=400,
                detail="Message was unroutable; the target does not route to any queue",
            ) from error
        if isinstance(error, ValueError):
            raise HTTPException(status_code=400, detail=str(error)) from error
        raise HTTPException(status_code=502, detail="Message operation failed") from error
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    OPERATION_SECONDS.labels(action=action).observe(elapsed_ms / 1000)
    ACTIONS.labels(action=action, result="success").inc()
    metadata: dict[str, object] = {"duration_ms": elapsed_ms, **mode_meta}
    if result.get("headers_added"):
        metadata["headers_added"] = result["headers_added"]
    if result.get("x_death"):
        metadata["x_death"] = result["x_death"]
    await audit.record(
        AuditEntry(
            username=username,
            action=action,
            timestamp=datetime.now(UTC),
            source_queue=source_queue,
            message_fingerprint=fingerprint,
            result="success",
            metadata=metadata,
            **target_fields,
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
    custom_headers = await _custom_headers(request)
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
            annotate=body.annotate,
            extra_headers=custom_headers,
        ),
        target=body.target.to_domain() if body.target else None,
        mode=body.mode,
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
        target=ReplayTarget(type="queue", queue=f"{body.source_queue}.parking"),
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


class PublishRequest(BaseModel):
    exchange: str = ""  # "" publishes via the default exchange straight to a queue
    routing_key: str = Field(min_length=1)
    payload: str = Field(max_length=1_048_576)
    mark_test: bool = True
    confirm: bool = False


@router.post("/publish")
async def publish(
    request: Request,
    body: PublishRequest,
    username: str = Depends(get_current_username),
) -> dict[str, object]:
    """Publish a hand-written test message (Composer). Mandatory publish — unroutable
    messages raise instead of being dropped, and every attempt is audited."""
    import json as jsonlib

    from aio_pika import Message

    if not body.confirm:
        raise HTTPException(status_code=400, detail="Publish confirmation is required")
    try:
        jsonlib.loads(body.payload)
        content_type = "application/json"
    except ValueError:
        content_type = "text/plain"
    headers: dict[str, Any] = {
        **(await _custom_headers(request)),
        "x-queuelens-published-by": username,
        "x-queuelens-published-at": datetime.now(UTC).isoformat(),
    }
    if body.mark_test:
        headers["x-queuelens-test"] = True
    target = (
        ReplayTarget(type="exchange", exchange=body.exchange, routing_key=body.routing_key)
        if body.exchange
        else ReplayTarget(type="queue", queue=body.routing_key)
    )
    target_fields: dict[str, str | None] = {
        "target_type": target.type,
        "target_queue": target.queue,
        "target_exchange": target.exchange,
        "target_routing_key": target.routing_key,
    }
    audit = request.app.state.audit_repository

    async def _record(result: str, error: str | None = None, **meta: object) -> None:
        await audit.record(
            AuditEntry(
                username=username,
                action="publish",
                timestamp=datetime.now(UTC),
                result=result,
                error_message=error,
                metadata={"content_type": content_type, "test": body.mark_test, **meta},
                **target_fields,
            )
        )

    started_at = time.perf_counter()
    try:
        connection = request.app.state.rabbitmq_connection
        async with connection.channel() as channel:
            message = Message(
                body=body.payload.encode("utf-8"),
                headers=headers,
                content_type=content_type,
            )
            if body.exchange:
                exchange = await channel.get_exchange(body.exchange, ensure=True)
                await exchange.publish(message, routing_key=body.routing_key)
            else:
                await cast(Any, channel).declare_queue(body.routing_key, passive=True)
                await channel.default_exchange.publish(message, routing_key=body.routing_key)
    except Exception as error:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        ACTIONS.labels(action="publish", result="failed").inc()
        await _record("failed", str(error), duration_ms=elapsed_ms)
        if isinstance(error, ChannelNotFoundEntity):
            raise HTTPException(
                status_code=404,
                detail="Target not found; check the exchange and routing key / queue",
            ) from error
        if isinstance(error, DeliveryError):
            raise HTTPException(
                status_code=400,
                detail="Message was unroutable; the exchange does not route this key to any queue",
            ) from error
        if isinstance(error, ValueError):
            raise HTTPException(status_code=400, detail=str(error)) from error
        raise HTTPException(status_code=502, detail="Publish failed") from error
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    ACTIONS.labels(action="publish", result="success").inc()
    await _record("success", duration_ms=elapsed_ms, headers_added=headers)
    return {
        "status": "success",
        "content_type": content_type,
        "target": {"exchange": body.exchange or None, "routing_key": body.routing_key},
        "duration_ms": elapsed_ms,
    }
