from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    if not getattr(request.app.state, "ready", False):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    connection = getattr(request.app.state, "rabbitmq_connection", None)
    if connection is not None and connection.is_started and not connection.is_connected:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ok"})
