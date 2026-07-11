import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.auth.basic import get_current_username

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
async def list_audit_events(
    request: Request,
    _username: str = Depends(get_current_username),
    action: str | None = Query(default=None),
    username: str | None = Query(default=None),
    source_queue: str | None = Query(default=None),
    result: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    entries = await request.app.state.audit_repository.list(
        action=action,
        username=username,
        source_queue=source_queue,
        result=result,
        limit=limit,
    )
    return {"events": entries}



@router.get("/export")
async def export_audit(
    request: Request,
    _username: str = Depends(get_current_username),
    format: Literal["csv", "json"] = Query(default="csv"),
) -> StreamingResponse:
    """Stream the complete audit history (not just the latest page)."""
    repository = request.app.state.audit_repository

    async def csv_stream() -> AsyncIterator[str]:
        yield (
            "id,timestamp,username,action,source_queue,"
            "target_queue,target_exchange,result,error\n"
        )
        async for event in repository.iter_all():
            fields = [
                event.get("id"), event.get("timestamp"), event.get("username"),
                event.get("action"), event.get("source_queue"), event.get("target_queue"),
                event.get("target_exchange"), event.get("result"), event.get("error_message"),
            ]
            yield ",".join(
                '"' + str("" if value is None else value).replace('"', '""') + '"'
                for value in fields
            ) + "\n"

    async def json_stream() -> AsyncIterator[str]:
        yield "["
        first = True
        async for event in repository.iter_all():
            prefix = "" if first else ","
            first = False
            yield prefix + json.dumps(event, default=str)
        yield "]"

    if format == "json":
        return StreamingResponse(
            json_stream(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=queuelens-audit.json"},
        )
    return StreamingResponse(
        csv_stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=queuelens-audit.csv"},
    )
