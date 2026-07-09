from pathlib import Path
from typing import cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.application.message_service import MessageService, message_to_dict
from app.application.queue_service import QueueService, queues_to_dicts
from app.auth.basic import get_current_username

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="login.html", context={})


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    queues = await cast(QueueService, request.app.state.queue_service).list_queues(dlq_only=True)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"queues": queues_to_dicts(queues)},
    )


@router.get("/queues/{queue_name}", response_class=HTMLResponse)
async def queue_detail(
    request: Request,
    queue_name: str,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    queue = await cast(QueueService, request.app.state.queue_service).get_queue(queue_name)
    messages = await cast(MessageService, request.app.state.message_service).list_messages(
        queue_name, request.app.state.settings.max_preview_messages
    )
    return templates.TemplateResponse(
        request=request,
        name="queue.html",
        context={
            "queue": queues_to_dicts([queue])[0],
            "messages": [message_to_dict(m) for m in messages],
        },
    )


@router.get("/messages/{queue_name}/{fingerprint}", response_class=HTMLResponse)
async def message_detail(
    request: Request,
    queue_name: str,
    fingerprint: str,
    _username: str = Depends(get_current_username),
) -> HTMLResponse:
    message = await cast(MessageService, request.app.state.message_service).get_message(
        queue_name, fingerprint, request.app.state.settings.max_preview_messages
    )
    return templates.TemplateResponse(
        request=request,
        name="message.html",
        context={
            "message": message_to_dict(
                message, request.app.state.settings.max_message_size_bytes
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
