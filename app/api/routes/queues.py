from typing import cast

from fastapi import APIRouter, Depends, Query, Request

from app.application.queue_service import QueueService, queues_to_dicts
from app.auth.basic import get_current_username

router = APIRouter(prefix="/api/queues", tags=["queues"])


def _service(request: Request) -> QueueService:
    return cast(QueueService, request.app.state.queue_service)


@router.get("")
async def list_queues(
    request: Request,
    _username: str = Depends(get_current_username),
    dlq_only: bool = Query(default=False),
) -> dict[str, object]:
    queues = await _service(request).list_queues(dlq_only=dlq_only)
    return {"queues": queues_to_dicts(queues)}


@router.get("/{queue_name}")
async def get_queue(
    request: Request,
    queue_name: str,
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    queue = await _service(request).get_queue(queue_name)
    return {"queue": queues_to_dicts([queue])[0]}
