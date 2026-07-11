from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from aiormq.exceptions import ChannelNotFoundEntity
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import actions, audit, bulk, health, messages, metrics, platform, queues
from app.application.alert_engine import AlertEngine
from app.application.environments import EnvironmentManager
from app.config import Settings, get_settings
from app.infrastructure.persistence.audit_repository import AuditRepository
from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.store import (
    AlertRuleRepository,
    BulkBatchRepository,
    NotificationRepository,
    SettingsRepository,
    UserRepository,
)
from app.infrastructure.rabbitmq.connection import RabbitMQUnavailableError
from app.infrastructure.rabbitmq.management_client import (
    RabbitMQManagementError,
)
from app.web import routes as web


def _error_response(request: Request, status_code: int, detail: str) -> Response:
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": detail}, status_code=status_code)
    return web.templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"status_code": status_code, "detail": detail},
        status_code=status_code,
    )


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LookupError)
    async def _lookup(request: Request, error: Exception) -> Response:
        return _error_response(request, 404, str(error))

    @app.exception_handler(ChannelNotFoundEntity)
    async def _queue_missing(request: Request, error: Exception) -> Response:
        return _error_response(request, 404, "Queue not found")

    @app.exception_handler(RabbitMQManagementError)
    async def _management(request: Request, error: Exception) -> Response:
        assert isinstance(error, RabbitMQManagementError)
        if error.status_code == 404:
            return _error_response(request, 404, "Queue not found")
        return _error_response(request, 502, str(error))

    @app.exception_handler(httpx.HTTPError)
    async def _management_unreachable(request: Request, error: Exception) -> Response:
        return _error_response(request, 503, "RabbitMQ Management API is unreachable")

    @app.exception_handler(RabbitMQUnavailableError)
    async def _amqp_unavailable(request: Request, error: Exception) -> Response:
        return _error_response(request, 503, "RabbitMQ connection is not available")


async def _retention_loop(app: FastAPI) -> None:
    import asyncio

    while True:
        try:
            retention = await app.state.settings_store.get("retention", {}) or {}
            days = int(retention.get("days") or 0)
            if days > 0:
                await app.state.audit_repository.delete_older_than(days)
                await app.state.notifications.purge_older_than(days)
        except Exception:  # noqa: BLE001 - retention must never kill the app
            pass
        await asyncio.sleep(3600)


async def _seed_defaults(app: FastAPI) -> None:
    settings = app.state.settings
    await app.state.users.seed_env_users(settings.users, settings.admin_username)
    if settings.smtp_host and not await app.state.settings_store.get("channels"):
        await app.state.settings_store.put(
            {
                "channels": {
                    "email": {
                        "smtp_host": settings.smtp_host,
                        "smtp_port": settings.smtp_port,
                        "from": "queuelens@local",
                        "to": "sre@queuelens.local",
                    }
                }
            }
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    import asyncio

    try:
        await app.state.database.start()
        await _seed_defaults(app)
        app.state.environment_manager.apply_custom(
            await app.state.settings_store.get("custom_environments", {}) or {}
        )
        stored_ui = await app.state.settings_store.get("ui", {}) or {}
        app.state.audit_repository.stream_to_log = bool(stored_ui.get("syslog"))
        await app.state.environment_manager.start_default()
        app.state.alert_engine.start()
        retention_task = asyncio.get_running_loop().create_task(_retention_loop(app))
        app.state.ready = True
        yield
    finally:
        app.state.ready = False
        retention_task.cancel()
        await app.state.alert_engine.stop()
        await app.state.environment_manager.stop_all()
        await app.state.database.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="QueueLens", version="0.5.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    database = Database(app.state.settings.database_url)
    app.state.database = database
    app.state.audit_repository = AuditRepository(database)
    app.state.settings_store = SettingsRepository(database, app.state.settings.secret_key)
    app.state.alert_rules = AlertRuleRepository(database)
    app.state.notifications = NotificationRepository(database)
    app.state.users = UserRepository(database)
    app.state.bulk_batches = BulkBatchRepository(database)
    manager = EnvironmentManager(app.state, app.state.settings, app.state.bulk_batches)
    app.state.environment_manager = manager
    manager.attach_default()  # services exist pre-lifespan so tests can override them
    app.state.alert_engine = AlertEngine(
        rules=app.state.alert_rules,
        notifications=app.state.notifications,
        settings_store=app.state.settings_store,
        get_queue_service=lambda: app.state.queue_service,
        interval_seconds=app.state.settings.alert_interval_seconds,
    )
    # base.html renders the environment badge and sidebar identity on every page
    web.templates.env.globals["app_environment"] = app.state.settings.environment
    web.templates.env.globals["admin_username"] = app.state.settings.admin_username
    web.templates.env.globals["app_version"] = app.version
    _register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(queues.router)
    app.include_router(audit.router)
    app.include_router(messages.router)
    app.include_router(actions.router)
    app.include_router(bulk.router)
    app.include_router(platform.router)
    app.include_router(web.router)
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "web" / "static"),
        name="static",
    )
    return app


app = create_app()
