from typing import cast

from fastapi import APIRouter, Depends, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.application.queue_service import QueueService
from app.auth.basic import get_current_username
from app.observability.metrics import DLQ_MESSAGES, RABBITMQ_READY

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(
    request: Request,
    _username: str = Depends(get_current_username),
) -> Response:
    connection = request.app.state.rabbitmq_connection
    RABBITMQ_READY.set(1 if (connection.is_started and connection.is_connected) else 0)
    try:
        queues = await cast(QueueService, request.app.state.queue_service).list_queues(
            dlq_only=True
        )
        DLQ_MESSAGES.clear()
        for queue in queues:
            DLQ_MESSAGES.labels(queue=queue.name).set(queue.messages)
    except Exception:  # broker or management API down — the ready gauge covers it
        RABBITMQ_READY.set(0)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
