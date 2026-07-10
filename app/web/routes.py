from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.application.message_service import MessageService, message_to_dict
from app.application.queue_service import QueueService, queues_to_dicts
from app.auth.basic import get_current_username
from app.observability.metrics import PREVIEW_REQUESTS

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _broker_display(rabbitmq_url: str, vhost: str) -> str:
    parsed = urlparse(rabbitmq_url)  # never echo credentials from the URL
    host = parsed.hostname or "rabbitmq"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{host}{port} · vhost {vhost}"


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="login.html", context={})


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    settings = request.app.state.settings
    queues = await cast(QueueService, request.app.state.queue_service).list_queues(dlq_only=True)
    queue_dicts = queues_to_dicts(queues)
    recent_events: list[dict[str, Any]] = []
    failed_today = 0
    try:
        audit = request.app.state.audit_repository
        recent_events = await audit.list(limit=8)
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        failed_today = await audit.count(result="failed", since=midnight)
    except Exception:  # audit store unavailable must not take the dashboard down
        pass
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "queues": queue_dicts,
            "largest": max(queue_dicts, key=lambda q: q["messages"], default=None),
            "recent_events": recent_events,
            "failed_today": failed_today,
            "broker": _broker_display(settings.rabbitmq_url, settings.rabbitmq_vhost),
            "preview_limit": settings.max_preview_messages,
        },
    )


@router.get("/queues/{queue_name}", response_class=HTMLResponse)
async def queue_detail(
    request: Request,
    queue_name: str,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    settings = request.app.state.settings
    queue = await cast(QueueService, request.app.state.queue_service).get_queue(queue_name)
    PREVIEW_REQUESTS.inc()
    messages = await cast(MessageService, request.app.state.message_service).list_messages(
        queue_name, settings.max_preview_messages
    )
    return templates.TemplateResponse(
        request=request,
        name="queue.html",
        context={
            "queue": queues_to_dicts([queue])[0],
            "messages": [
                message_to_dict(m, masked_fields=settings.masked_field_names) for m in messages
            ],
        },
    )


@router.get("/messages/{queue_name}/{fingerprint}", response_class=HTMLResponse)
async def message_detail(
    request: Request,
    queue_name: str,
    fingerprint: str,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    settings = request.app.state.settings
    message = await cast(MessageService, request.app.state.message_service).get_message(
        queue_name, fingerprint, settings.refetch_window_size
    )
    return templates.TemplateResponse(
        request=request,
        name="message.html",
        context={
            "message": message_to_dict(
                message,
                settings.max_message_size_bytes,
                masked_fields=settings.masked_field_names,
            )
        },
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    events = await request.app.state.audit_repository.list(limit=100)
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={"events": events},
    )
