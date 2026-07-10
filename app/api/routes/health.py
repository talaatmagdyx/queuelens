from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.basic import get_current_username

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/broker")
async def broker(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    settings = request.app.state.settings
    parsed = urlparse(settings.rabbitmq_url)  # never echo credentials
    host = parsed.hostname or "rabbitmq"
    version = None
    try:
        overview = await request.app.state.management_client.overview()
        version = overview.get("rabbitmq_version")
    except Exception:
        pass
    return {
        "host": f"{host}:{parsed.port}" if parsed.port else host,
        "vhost": settings.rabbitmq_vhost,
        "rabbitmq_version": version,
        "environment": settings.environment,
    }


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    if not getattr(request.app.state, "ready", False):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    connection = getattr(request.app.state, "rabbitmq_connection", None)
    if connection is not None and connection.is_started and not connection.is_connected:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ok"})
