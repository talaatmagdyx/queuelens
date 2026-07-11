from pathlib import Path
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


@router.get("/api/users")
async def users(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    settings = request.app.state.settings
    users_repo = getattr(request.app.state, "users", None)
    if users_repo is not None:
        try:
            stored = await users_repo.list()
        except Exception:  # noqa: BLE001 - fall back to env accounts pre-migration
            stored = []
        if stored:
            return {
                "accounts": [
                    {
                        "username": u["username"],
                        "role": "Administrator" if u["role"] == "Admin" else u["role"],
                        "email": u["email"],
                        "invited_by": u["invited_by"],
                        "active": u["active"],
                    }
                    for u in stored
                ]
            }
    return {
        "accounts": [
            {
                "username": name,
                "role": "Administrator" if name == settings.admin_username else "Operator",
            }
            for name in sorted(settings.users)
        ]
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
            # internal exchanges (e.g. amq.rabbitmq.trace) refuse direct publishes
            if not item.get("internal", False)
        ]
    }


@router.get("/api/config")
async def config(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    """Read-only runtime configuration (env-var driven; secrets never included)."""
    settings = request.app.state.settings
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "auth_enabled": settings.auth_enabled,
        "management_url": settings.rabbitmq_management_url,
        "vhost": settings.rabbitmq_vhost,
        "connection_name": settings.rabbitmq_connection_name,
        "operation_timeout_seconds": settings.rabbitmq_operation_timeout_seconds,
        "max_preview_messages": settings.max_preview_messages,
        "max_message_size_bytes": settings.max_message_size_bytes,
        "refetch_window_size": settings.refetch_window_size,
        "max_bulk_size": settings.max_bulk_size,
        "bulk_dry_run_ttl_seconds": settings.bulk_dry_run_ttl_seconds,
        "masking_enabled": settings.masking_enabled,
        "masked_fields": list(settings.masked_field_names),
        "replay_targets": sorted(settings.replay_targets),
    }


@router.get("/api/topology")
async def topology(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    """Exchanges, bindings, and queues (with dead-letter args) for the topology view."""
    client = request.app.state.management_client
    exchanges_raw = await client.list_exchanges()
    bindings_raw = await client.list_bindings()
    queues_raw = await client.list_queues()
    return {
        "exchanges": [
            {"name": e.get("name", ""), "type": e.get("type", "direct")}
            for e in exchanges_raw
            if e.get("name") and not e.get("internal", False)
        ],
        "bindings": [
            {
                "source": b.get("source", ""),
                "destination": b.get("destination", ""),
                "destination_type": b.get("destination_type", "queue"),
                "routing_key": b.get("routing_key", ""),
            }
            for b in bindings_raw
        ],
        "queues": [
            {
                "name": q.get("name", ""),
                "consumers": q.get("consumers", 0),
                "messages": q.get("messages", 0),
                "dlx": (q.get("arguments") or {}).get("x-dead-letter-exchange"),
                "dlx_routing_key": (q.get("arguments") or {}).get("x-dead-letter-routing-key"),
            }
            for q in queues_raw
        ],
    }


@router.get("/api/alert-rules")
async def alert_rules(
    _username: str = Depends(get_current_username),
) -> dict[str, object]:
    """Read-only view of the packaged Prometheus alert rules (deploy/prometheus/alerts.yml)."""
    import yaml

    path = Path(__file__).resolve().parents[3] / "deploy" / "prometheus" / "alerts.yml"
    if not path.exists():
        return {"rules": [], "source": None}
    parsed = yaml.safe_load(path.read_text())
    rules = []
    for group in (parsed or {}).get("groups", []):
        for rule in group.get("rules", []):
            annotations = rule.get("annotations") or {}
            rules.append(
                {
                    "name": rule.get("alert", ""),
                    "expr": rule.get("expr", ""),
                    "for": rule.get("for", "0m"),
                    "severity": (rule.get("labels") or {}).get("severity", "warning"),
                    "summary": annotations.get("summary", ""),
                    "description": annotations.get("description", ""),
                }
            )
    return {"rules": rules, "source": "deploy/prometheus/alerts.yml"}


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
