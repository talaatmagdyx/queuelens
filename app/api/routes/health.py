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


@router.get("/api/exchanges")
async def exchanges(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    raw = await request.app.state.management_client.list_exchanges()
    return {
        "exchanges": [
            {"name": item.get("name", ""), "type": item.get("type", "direct")}
            for item in raw
        ]
    }


@router.get("/api/broker/test")
async def broker_test(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    """Live connectivity check: Management API round-trip plus AMQP state."""
    import time

    connection = request.app.state.rabbitmq_connection
    started = time.perf_counter()
    management_ok = False
    detail: dict[str, object] = {}
    try:
        overview = await request.app.state.management_client.overview()
        management_ok = True
        detail = {
            "rabbitmq_version": overview.get("rabbitmq_version"),
            "cluster_name": overview.get("cluster_name"),
            "queues": (overview.get("object_totals") or {}).get("queues"),
            "nodes": len(overview.get("listeners") or []) or None,
        }
    except Exception as error:
        detail = {"error": str(error) or "Management API unreachable"}
    latency_ms = round((time.perf_counter() - started) * 1000)
    amqp_ok = bool(connection.is_started and connection.is_connected)
    return {
        "ok": management_ok and amqp_ok,
        "management_api": management_ok,
        "amqp": amqp_ok,
        "latency_ms": latency_ms,
        **detail,
    }


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    if not getattr(request.app.state, "ready", False):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    connection = getattr(request.app.state, "rabbitmq_connection", None)
    if connection is not None and connection.is_started and not connection.is_connected:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ok"})
