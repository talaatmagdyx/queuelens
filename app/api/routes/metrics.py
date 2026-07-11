from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.application.queue_service import QueueService
from app.auth.basic import get_current_username
from app.observability.metrics import (
    ACTIONS,
    DLQ_MESSAGES,
    OPERATION_SECONDS,
    PREVIEW_REQUESTS,
    RABBITMQ_READY,
)

router = APIRouter(tags=["metrics"])

ALERT_RULES_FILE = Path(__file__).resolve().parents[3] / "deploy" / "prometheus" / "alerts.yml"


def _samples(metric: Any) -> list[Any]:
    return list(next(iter(metric.collect())).samples)


async def _refresh_gauges(request: Request) -> None:
    """Point-in-time gauges are refreshed at read time so they reflect the broker right now."""
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


@router.get("/metrics")
async def metrics(
    request: Request,
    _username: str = Depends(get_current_username),
) -> Response:
    await _refresh_gauges(request)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/metrics/summary")
async def metrics_summary(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    """The queuelens_* metrics as JSON, for the console's Metrics screen."""
    await _refresh_gauges(request)

    dlq = [
        {"queue": s.labels["queue"], "messages": int(s.value)}
        for s in _samples(DLQ_MESSAGES)
    ]
    dlq.sort(key=lambda row: -row["messages"])

    actions = [
        {"action": s.labels["action"], "result": s.labels["result"], "count": int(s.value)}
        for s in _samples(ACTIONS)
        if s.name.endswith("_total")
    ]
    actions.sort(key=lambda row: -row["count"])

    op_sums: dict[str, float] = {}
    op_counts: dict[str, int] = {}
    for s in _samples(OPERATION_SECONDS):
        if s.name.endswith("_sum"):
            op_sums[s.labels["action"]] = s.value
        elif s.name.endswith("_count"):
            op_counts[s.labels["action"]] = int(s.value)
    operations = [
        {
            "action": action,
            "count": count,
            "avg_seconds": round(op_sums.get(action, 0.0) / count, 4) if count else 0.0,
        }
        for action, count in sorted(op_counts.items())
    ]

    previews = next(
        (s.value for s in _samples(PREVIEW_REQUESTS) if s.name.endswith("_total")),
        0.0,
    )

    return {
        "rabbitmq_ready": _samples(RABBITMQ_READY)[0].value == 1,
        "dlq": dlq,
        "dlq_backlog": sum(row["messages"] for row in dlq),
        "preview_requests": int(previews),
        "actions": actions,
        "actions_succeeded": sum(a["count"] for a in actions if a["result"] == "success"),
        "actions_failed": sum(a["count"] for a in actions if a["result"] == "failed"),
        "operations": operations,
    }


@router.get("/api/metrics/alert-rules")
async def alert_rules(
    _username: str = Depends(get_current_username),
) -> PlainTextResponse:
    """The bundled example Prometheus alert rules, verbatim."""
    return PlainTextResponse(ALERT_RULES_FILE.read_text())
