from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.application.message_service import MessageService, message_to_dict
from app.auth.basic import get_current_username
from app.observability.metrics import PREVIEW_REQUESTS

router = APIRouter(prefix="/api/queues", tags=["messages"])


def _service(request: Request) -> MessageService:
    return cast(MessageService, request.app.state.message_service)


@router.get("/{queue_name}/messages")
async def list_messages(
    request: Request,
    queue_name: str,
    _username: str = Depends(get_current_username),
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> dict[str, object]:
    settings = request.app.state.settings
    stored = await request.app.state.settings_store.get("limits", {}) or {}
    effective = limit or int(
        stored.get("max_preview_messages") or settings.max_preview_messages
    )
    PREVIEW_REQUESTS.inc()
    messages = await _service(request).list_messages(queue_name, min(effective, 1000))
    return {
        "messages": [
            message_to_dict(
                message,
                settings.max_message_size_bytes,
                masked_fields=settings.masked_field_names,
            )
            for message in messages
        ]
    }


@router.get("/{queue_name}/messages/{fingerprint}")
async def get_message(
    request: Request,
    queue_name: str,
    fingerprint: str = Path(min_length=8),
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    settings = request.app.state.settings
    try:
        message = await _service(request).get_message(
            queue_name,
            fingerprint,
            settings.refetch_window_size,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "message": message_to_dict(
            message,
            settings.max_message_size_bytes,
            masked_fields=settings.masked_field_names,
        )
    }
