from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.api.scope import broker, broker_scope
from app.application.message_service import MessageService, message_to_dict
from app.application.queue_service import UnsafeToBrowse
from app.auth.basic import get_current_username
from app.observability.metrics import PREVIEW_REQUESTS

router = APIRouter(
    prefix="/api/queues", tags=["messages"], dependencies=[Depends(broker_scope)]
)

HARD_CEILING = 1000


async def effective_limit(request: Request, key: str) -> int:
    """A stored override (Configuration → Limits) wins over the env-var default;
    1000 is the absolute ceiling. Callers may ask for less, never for more."""
    stored = await request.app.state.settings_store.get_safe("limits", {}) or {}
    value = stored.get(key) or getattr(request.app.state.settings, key)
    return max(1, min(int(value), HARD_CEILING))


def _service(request: Request) -> MessageService:
    return cast(MessageService, broker(request).message_service)


@router.get("/{queue_name}/messages")
async def list_messages(
    request: Request,
    queue_name: str,
    _username: str = Depends(get_current_username),
    limit: int | None = Query(default=None, ge=1, le=HARD_CEILING),
) -> dict[str, object]:
    settings = request.app.state.settings
    cap = await effective_limit(request, "max_preview_messages")
    PREVIEW_REQUESTS.inc()
    try:
        messages = await _service(request).list_messages(queue_name, min(limit or cap, cap))
    except UnsafeToBrowse as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
    refetch = await effective_limit(request, "refetch_window_size")
    try:
        message = await _service(request).get_message(queue_name, fingerprint, refetch)
    except UnsafeToBrowse as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "message": message_to_dict(
            message,
            settings.max_message_size_bytes,
            masked_fields=settings.masked_field_names,
        )
    }
