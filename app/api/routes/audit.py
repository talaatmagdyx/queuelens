from fastapi import APIRouter, Depends, Query, Request

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

