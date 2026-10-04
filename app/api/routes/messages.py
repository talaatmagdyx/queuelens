import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse

from app.api.scope import broker, broker_scope
from app.application.message_service import MessageService, message_to_dict
from app.application.queue_service import UnsafeToBrowse
from app.application.snapshots import Snapshot
from app.auth.basic import get_current_username
from app.domain.models import AuditEntry, MessageRecord
from app.observability.metrics import PREVIEW_REQUESTS

router = APIRouter(
    prefix="/api/queues", tags=["messages"], dependencies=[Depends(broker_scope)]
)

HARD_CEILING = 1000
# one snapshot scan may go deeper than a page: every message it reads is held unacked
CEILINGS = {"max_browse_depth": 50_000}


async def effective_limit(request: Request, key: str) -> int:
    """A stored override (Configuration → Limits) wins over the env-var default, within
    the key's ceiling (1000 unless CEILINGS says otherwise). Callers may ask for less."""
    stored = await request.app.state.settings_store.get_safe("limits", {}) or {}
    value = stored.get(key) or getattr(request.app.state.settings, key)
    return max(1, min(int(value), CEILINGS.get(key, HARD_CEILING)))


def _service(request: Request) -> MessageService:
    return cast(MessageService, broker(request).message_service)


def _snapshot(request: Request, snapshot_id: str, queue_name: str) -> Snapshot:
    found = request.app.state.snapshots.get(snapshot_id, request.state.scope, queue_name)
    if found is None:
        raise HTTPException(
            status_code=404, detail="Snapshot expired or unknown — scan the queue again"
        )
    return cast(Snapshot, found)


async def scan_depth(
    request: Request, queue_name: str, fingerprints: list[str], snapshot_id: str | None
) -> int:
    """How far an action scans: the refetch window — or, for messages picked from a
    snapshot, down to the deepest of them (plus the window as slack), so anything a
    snapshot showed can be acted on. Messages only move up a classic queue."""
    window = await effective_limit(request, "refetch_window_size")
    found = snapshot_id and request.app.state.snapshots.get(
        snapshot_id, request.state.scope, queue_name
    )
    if not found:
        return window
    deepest = max((found.positions.get(fp, -1) for fp in fingerprints), default=-1)
    depth = await effective_limit(request, "max_browse_depth")
    return min(depth, max(window, deepest + 1 + window))


@router.get("/{queue_name}/messages")
async def list_messages(
    request: Request,
    queue_name: str,
    _username: str = Depends(get_current_username),
    limit: int | None = Query(default=None, ge=1, le=HARD_CEILING),
    snapshot: str | None = Query(default=None, max_length=64),
    offset: int = Query(default=0, ge=0),
    contains: str | None = Query(default=None, max_length=256),
    payload_format: str | None = Query(default=None, pattern="^(json|text|base64)$"),
    min_deaths: int | None = Query(default=None, ge=1),
) -> dict[str, object]:
    """The head of the queue (`limit` messages), or — with `snapshot=new` — one scan down
    to the browse depth, kept for a few minutes and paged with `snapshot=<id>`, `offset`,
    `limit` and filters without touching the broker again."""
    settings = request.app.state.settings
    cap = await effective_limit(request, "max_preview_messages")
    page = min(limit or cap, cap)
    depth = await effective_limit(request, "max_browse_depth")

    def render(records: list[MessageRecord]) -> list[dict[str, object]]:
        return [
            message_to_dict(
                record, settings.max_message_size_bytes, masked_fields=settings.masked_field_names
            )
            for record in records
        ]

    PREVIEW_REQUESTS.inc()
    try:
        if snapshot is None and not (offset or contains or payload_format or min_deaths):
            records = await _service(request).list_messages(queue_name, page, depth=depth)
            return {"messages": render(records)}
        if snapshot is None or snapshot == "new":  # paging and filters work on a snapshot
            scan = await _service(request).snapshot(queue_name, depth)
            found = request.app.state.snapshots.add(
                request.state.scope, queue_name, scan.records,
                ready=scan.ready, stopped=scan.stopped, depth=depth,
            )
        else:
            found = _snapshot(request, snapshot, queue_name)
    except UnsafeToBrowse as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    matches = found.matching(contains, payload_format, min_deaths)
    return {
        "messages": render(matches[offset : offset + page]),
        "total": len(matches),
        "offset": offset,
        "limit": page,
        "snapshot": found.meta(),
    }


@router.get("/{queue_name}/messages/{fingerprint}")
async def get_message(
    request: Request,
    queue_name: str,
    fingerprint: str = Path(min_length=8),
    _username: str = Depends(get_current_username),
    snapshot: str | None = Query(default=None, max_length=64),
) -> dict[str, object]:
    settings = request.app.state.settings
    refetch = await effective_limit(request, "refetch_window_size")
    found = snapshot and request.app.state.snapshots.get(
        snapshot, request.state.scope, queue_name
    )
    try:
        if found and fingerprint in found.positions:
            message = found.records[found.positions[fingerprint]]  # no broker read
        else:
            depth = await effective_limit(request, "max_browse_depth")
            message = await _service(request).get_message(
                queue_name, fingerprint, refetch, depth=depth
            )
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


EXPORT_COLUMNS = ("fingerprint", "message_id", "correlation_id", "timestamp", "exchange",
                  "routing_key", "deaths", "payload_format", "payload", "headers")


@router.get("/{queue_name}/snapshots/{snapshot_id}/export")
async def export_snapshot(
    request: Request,
    queue_name: str,
    snapshot_id: str = Path(max_length=64),
    username: str = Depends(get_current_username),
    format: Literal["json", "csv"] = Query(default="json"),
    contains: str | None = Query(default=None, max_length=256),
    payload_format: str | None = Query(default=None, pattern="^(json|text|base64)$"),
    min_deaths: int | None = Query(default=None, ge=1),
) -> StreamingResponse:
    """Download a snapshot's messages (the same filters as its pages), rendered and masked
    as the console shows them. Nothing more is read from the broker; the export is
    audited, since it hands over message bodies in bulk."""
    from app.api.routes.audit import csv_cell

    settings = request.app.state.settings
    found = _snapshot(request, snapshot_id, queue_name)
    matches = found.matching(contains, payload_format, min_deaths)
    await request.app.state.audit_repository.record(AuditEntry(
        username=username, action="export_snapshot", timestamp=datetime.now(UTC),
        source_queue=queue_name, result="success",
        metadata={"snapshot": snapshot_id, "messages": len(matches), "format": format},
    ))

    def rendered() -> Iterator[dict[str, object]]:
        for record in matches:
            yield message_to_dict(
                record, settings.max_message_size_bytes, masked_fields=settings.masked_field_names
            )

    def as_json() -> Iterator[str]:
        yield "["
        for index, message in enumerate(rendered()):
            yield ("," if index else "") + json.dumps(message, default=str)
        yield "]"

    def as_csv() -> Iterator[str]:
        yield ",".join(EXPORT_COLUMNS) + "\n"
        for message in rendered():
            payload = message["payload"]
            row = {
                **message,
                "payload": payload if isinstance(payload, str) else json.dumps(payload),
                "headers": json.dumps(message["headers"], default=str),
            }
            yield ",".join(csv_cell(row.get(column)) for column in EXPORT_COLUMNS) + "\n"

    name = re.sub(r"[^A-Za-z0-9._-]", "_", queue_name)  # a queue name is user data
    return StreamingResponse(
        as_json() if format == "json" else as_csv(),
        media_type="application/json" if format == "json" else "text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}-snapshot.{format}"},
    )
