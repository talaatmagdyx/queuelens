from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.application.message_service import MessageService, message_to_dict
from app.application.queue_service import QueueService, queues_to_dicts
from app.auth.basic import get_current_username
from app.observability.metrics import PREVIEW_REQUESTS


def _identity_context(request: Request) -> dict[str, Any]:
    """Expose the acting Basic Auth username to every template (topbar chip)."""
    import base64

    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8", "replace")
            return {"current_user": decoded.split(":", 1)[0]}
        except Exception:
            pass
    return {"current_user": None}


router = APIRouter(tags=["web"])
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
    context_processors=[_identity_context],
)


def _broker_display(rabbitmq_url: str, vhost: str) -> str:
    parsed = urlparse(rabbitmq_url)  # never echo credentials from the URL
    host = parsed.hostname or "rabbitmq"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{host}{port} · vhost {vhost}"


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="login.html", context={})


@router.get("/app")
async def spa(
    _username: str = Depends(get_current_username),
) -> RedirectResponse:
    """The claude.ai/design UI kit, served as a single-page app wired to the
    live API. Auth here forces the Basic Auth prompt before the static SPA
    loads, so its data layer inherits browser credentials."""
    return RedirectResponse(url="/static/ds/ui_kits/queuelens/index.html")


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
    success_rate_today = None
    try:
        audit = request.app.state.audit_repository
        recent_events = await audit.list(limit=8)
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        failed_today = await audit.count(result="failed", since=midnight)
        succeeded_today = await audit.count(result="success", since=midnight)
        if failed_today + succeeded_today:
            success_rate_today = round(
                100 * succeeded_today / (failed_today + succeeded_today), 1
            )
    except Exception:  # audit store unavailable must not take the dashboard down
        pass
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "queues": queue_dicts,
            "largest": max(queue_dicts, key=lambda q: q["messages"], default=None),
            "no_consumers": sum(1 for q in queue_dicts if q["consumers"] == 0),
            "recent_events": recent_events,
            "failed_today": failed_today,
            "success_rate_today": success_rate_today,
            "broker": _broker_display(settings.rabbitmq_url, settings.rabbitmq_vhost),
            "preview_limit": settings.max_preview_messages,
        },
    )


@router.get("/queues", response_class=HTMLResponse)
async def queues_index(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    settings = request.app.state.settings
    queues = await cast(QueueService, request.app.state.queue_service).list_queues()
    queue_dicts = queues_to_dicts(queues)
    return templates.TemplateResponse(
        request=request,
        name="queues.html",
        context={
            "queues": queue_dicts,
            "dlq_count": sum(1 for q in queue_dicts if q["is_dlq"]),
            "with_consumers": sum(1 for q in queue_dicts if q["consumers"] > 0),
            "with_messages": sum(1 for q in queue_dicts if q["messages"] > 0),
            "broker": _broker_display(settings.rabbitmq_url, settings.rabbitmq_vhost),
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


@router.get("/messages", response_class=HTMLResponse)
async def messages_index(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    queues = await cast(QueueService, request.app.state.queue_service).list_queues(dlq_only=True)
    return templates.TemplateResponse(
        request=request,
        name="messages.html",
        context={"queues": queues_to_dicts(queues)},
    )


@router.get("/replay", response_class=HTMLResponse)
async def replay_hub(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    queues = await cast(QueueService, request.app.state.queue_service).list_queues(dlq_only=True)
    events: list[dict[str, Any]] = []
    try:
        events = await request.app.state.audit_repository.list(action="replay", limit=15)
    except Exception:
        pass
    return templates.TemplateResponse(
        request=request,
        name="replay_hub.html",
        context={"queues": queues_to_dicts(queues), "events": events},
    )


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    settings = request.app.state.settings
    accounts = [
        {
            "username": name,
            "role": "Administrator" if name == settings.admin_username else "Operator",
        }
        for name in sorted(settings.users)
    ]
    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={"accounts": accounts},
    )


@router.get(
    "/messages/{queue_name}/{fingerprint}/{wizard}",
    response_class=HTMLResponse,
)
async def action_wizard(
    request: Request,
    queue_name: str,
    fingerprint: str,
    wizard: str,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    if wizard not in {"replay", "park"}:
        raise LookupError(f"Unknown wizard: {wizard}")
    settings = request.app.state.settings
    message = await cast(MessageService, request.app.state.message_service).get_message(
        queue_name, fingerprint, settings.refetch_window_size
    )
    return templates.TemplateResponse(
        request=request,
        name=f"wizard_{wizard}.html",
        context={
            "message": message_to_dict(
                message,
                settings.max_message_size_bytes,
                masked_fields=settings.masked_field_names,
            ),
            "vhost": settings.rabbitmq_vhost,
        },
    )


@router.get("/notifications", response_class=HTMLResponse)
async def notifications(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    """Derived from live state: queue severities plus recent audit outcomes."""
    items: list[dict[str, Any]] = []
    queues = await cast(QueueService, request.app.state.queue_service).list_queues(dlq_only=True)
    for queue in queues_to_dicts(queues):
        if queue["severity"] == "attention":
            items.append(
                {
                    "id": f"queue:{queue['name']}",
                    "level": "alert",
                    "title": "DLQ queue needs attention",
                    "message": f"{queue['name']} has {queue['messages']} messages",
                    "source": "Queue Monitor",
                    "timestamp": None,
                    "href": f"/queues/{queue['name']}",
                }
            )
        elif queue["severity"] == "warning":
            items.append(
                {
                    "id": f"queue:{queue['name']}",
                    "level": "warning",
                    "title": "DLQ queue is growing",
                    "message": f"{queue['name']} has {queue['messages']} messages",
                    "source": "Queue Monitor",
                    "timestamp": None,
                    "href": f"/queues/{queue['name']}",
                }
            )
    try:
        events = await request.app.state.audit_repository.list(limit=30)
    except Exception:
        events = []
    for event in events:
        if event["result"] == "started":
            continue
        level = "success" if event["result"] == "success" else (
            "alert" if event["result"] in ("failed", "partial") else "info"
        )
        title = {
            "success": f"{event['action']} action successful",
            "alert": f"Failed {event['action']} action",
            "info": f"{event['action']} — {event['result']}",
        }[level]
        target = event.get("target_queue") or event.get("target_exchange")
        items.append(
            {
                "id": f"audit:{event['id']}",
                "level": level,
                "title": title,
                "message": (event.get("source_queue") or "—")
                + (f" → {target}" if target else ""),
                "source": "Audit Log",
                "timestamp": event.get("timestamp"),
                "href": "/audit",
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={"items": items},
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    events = await request.app.state.audit_repository.list(limit=500)
    results = [str(event["result"]) for event in events]
    succeeded = results.count("success")
    failed = results.count("failed") + results.count("partial")
    outcomes = succeeded + failed
    started = results.count("started")
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "events": events,
            "stats": {
                "total": len(events),
                "succeeded": succeeded,
                "failed": failed,
                "in_progress": max(0, started - outcomes),
                "success_rate": round(100 * succeeded / outcomes, 1) if outcomes else None,
                "users": len({event["username"] for event in events}),
            },
        },
    )


@router.get("/config", response_class=HTMLResponse)
async def configuration(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    settings = request.app.state.settings
    amqp = urlparse(settings.rabbitmq_url)
    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "connection": {
                "Management API URL": settings.rabbitmq_management_url,
                "AMQP host": f"{amqp.hostname or 'rabbitmq'}"
                + (f":{amqp.port}" if amqp.port else ""),
                "Virtual host": settings.rabbitmq_vhost,
                "AMQP user": amqp.username or "guest",
                "Management user": settings.rabbitmq_management_username,
                "Operation timeout": f"{settings.rabbitmq_operation_timeout_seconds:g}s",
            },
            "limits": {
                "Message preview limit": settings.max_preview_messages,
                "Payload display limit": f"{settings.max_message_size_bytes} B",
                "Re-fetch window": settings.refetch_window_size,
                "Bulk scan window": settings.max_bulk_size,
                "Bulk dry-run TTL": f"{settings.bulk_dry_run_ttl_seconds}s",
            },
            "masking": {
                "Masking enabled": "yes" if settings.masking_enabled else "no",
                "Masked fields": ", ".join(settings.masked_field_names) or "—",
            },
            "general": {
                "Application": settings.app_name,
                "Environment": settings.environment,
                "Auth enabled": "yes" if settings.auth_enabled else "no",
                "Audit store": settings.database_url.split("://")[0],
            },
        },
    )
