from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.application.message_service import MessageService, message_to_dict
from app.auth.basic import get_current_username

router = APIRouter(prefix="/api/queues", tags=["messages"])


def _service(request: Request) -> MessageService:
    return cast(MessageService, request.app.state.message_service)


@router.get("/{queue_name}/messages")
async def list_messages(
    request: Request,
    queue_name: str,
    _username: str = Depends(get_current_username),
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, object]:
    messages = await _service(request).list_messages(queue_name, limit)
    return {
        "messages": [
            message_to_dict(message, request.app.state.settings.max_message_size_bytes)
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
    try:
        message = await _service(request).get_message(
            queue_name,
            fingerprint,
            request.app.state.settings.refetch_window_size,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "message": message_to_dict(
            message, request.app.state.settings.max_message_size_bytes
        )
    }
