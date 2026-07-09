from typing import cast

from fastapi import APIRouter, Query, Request

from app.application.queue_service import QueueService, queues_to_dicts

router = APIRouter(prefix="/api/queues", tags=["queues"])


def _service(request: Request) -> QueueService:
    return cast(QueueService, request.app.state.queue_service)


@router.get("")
async def list_queues(
    request: Request,
    dlq_only: bool = Query(default=False),
) -> dict[str, object]:
    queues = await _service(request).list_queues(dlq_only=dlq_only)
    return {"queues": queues_to_dicts(queues)}


@router.get("/{queue_name}")
async def get_queue(request: Request, queue_name: str) -> dict[str, object]:
    queue = await _service(request).get_queue(queue_name)
    return {"queue": queues_to_dicts([queue])[0]}
