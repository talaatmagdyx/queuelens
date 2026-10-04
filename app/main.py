import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from aiormq.exceptions import ChannelNotFoundEntity
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.routes import (
    actions,
    audit,
    bulk,
    health,
    messages,
    metrics,
    platform,
    policies,
    queues,
)
from app.application.alert_engine import AlertEngine
from app.application.environments import EnvironmentManager
from app.application.queue_service import UnsafeToBrowse
from app.application.replay_policies import PolicyRunner
from app.application.snapshots import SnapshotStore
from app.auth.proxy import KeepPeer
from app.config import Settings, get_settings
from app.infrastructure.persistence.audit_repository import REQUEST_CONTEXT, AuditRepository
from app.infrastructure.persistence.coordination import Coordinator
from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.store import (
    AlertRuleRepository,
    BulkBatchRepository,
    LoginFailureRepository,
    NotificationRepository,
    ReplayPolicyRepository,
    SettingsRepository,
    UserRepository,
)
from app.infrastructure.rabbitmq.connection import RabbitMQUnavailableError
from app.infrastructure.rabbitmq.management_client import (
    RabbitMQManagementError,
)
from app.web import routes as web

logger = logging.getLogger(__name__)


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

    @app.exception_handler(UnsafeToBrowse)
    async def _unsafe_to_browse(request: Request, error: Exception) -> Response:
        return _error_response(request, 409, str(error))


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


async def _init_database(app: FastAPI) -> None:
    """Tables, then the seeded accounts and channels. Replicas starting together on an
    empty PostgreSQL take turns: the schema under its own lock, the seeding under this one."""
    await app.state.database.start()
    async with app.state.coordinator.lock("startup"):
        await _seed_defaults(app)
        for repository in (app.state.replay_policies, app.state.alert_rules):
            await repository.adopt_unscoped(*app.state.environment_manager.default_key)


SYNC_SECONDS = 5.0


async def _sync_settings(app: FastAPI) -> None:
    """Settings this replica keeps in memory, as another replica may have changed them:
    runtime environments (a removed one stops working everywhere) and the audit stream."""
    store = app.state.settings_store
    await app.state.environment_manager.sync_custom(
        await store.get("custom_environments", {}) or {}
    )
    stored_ui = await store.get("ui", {}) or {}
    app.state.audit_repository.stream_to_log = bool(stored_ui.get("syslog"))


async def _sync_loop(app: FastAPI) -> None:
    import asyncio

    while True:
        await asyncio.sleep(SYNC_SECONDS)
        try:
            await _sync_settings(app)
        except Exception:  # noqa: BLE001 - a database hiccup must not stop the loop
            logger.warning("settings sync failed", exc_info=True)


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

    # None until started, so a failed startup reports its real cause
    retention_task: asyncio.Task[None] | None = None
    sync_task: asyncio.Task[None] | None = None
    try:
        await _init_database(app)
        await _sync_settings(app)
        await app.state.environment_manager.start_default()
        app.state.alert_engine.start()
        app.state.policy_runner.start()
        retention_task = asyncio.get_running_loop().create_task(_retention_loop(app))
        sync_task = asyncio.get_running_loop().create_task(_sync_loop(app))
        app.state.ready = True
        yield
    finally:
        app.state.ready = False
        for task in (retention_task, sync_task):
            if task is not None:
                task.cancel()
        await app.state.alert_engine.stop()
        await app.state.policy_runner.stop()
        await app.state.environment_manager.stop_all()
        await app.state.coordinator.close()
        await app.state.database.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    # API docs and schema sit behind the same auth as the API (registered below)
    app = FastAPI(
        title="QueueLens",
        version="0.18.1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings or get_settings()
    database = Database(app.state.settings.database_url)
    app.state.database = database
    app.state.audit_repository = AuditRepository(database)
    app.state.settings_store = SettingsRepository(database, app.state.settings.secret_key)
    app.state.alert_rules = AlertRuleRepository(database)
    app.state.notifications = NotificationRepository(database)
    app.state.users = UserRepository(database)
    app.state.login_failures = LoginFailureRepository(database)
    app.state.replay_policies = ReplayPolicyRepository(database)
    app.state.coordinator = Coordinator(database)
    app.state.bulk_batches = BulkBatchRepository(database)
    app.state.snapshots = SnapshotStore()
    manager = EnvironmentManager(
        app.state, app.state.settings, app.state.bulk_batches, app.state.coordinator
    )
    app.state.environment_manager = manager
    manager.attach_default()  # services exist pre-lifespan so tests can override them
    app.state.alert_engine = AlertEngine(
        rules=app.state.alert_rules,
        notifications=app.state.notifications,
        settings_store=app.state.settings_store,
        queue_service_for=manager.queue_service_for,
        interval_seconds=app.state.settings.alert_interval_seconds,
        is_leader=app.state.coordinator.is_leader,
    )
    # the replica that evaluates alerts also runs replay policies
    app.state.policy_runner = PolicyRunner(app.state, is_leader=app.state.coordinator.is_leader)
    # base.html renders the environment badge and sidebar identity on every page
    web.templates.env.globals["app_environment"] = app.state.settings.environment
    web.templates.env.globals["admin_username"] = app.state.settings.admin_username
    web.templates.env.globals["app_version"] = app.version
    _register_error_handlers(app)

    @app.middleware("http")
    async def _audit_request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        REQUEST_CONTEXT.set(
            (request.client.host if request.client else None, request.headers.get("user-agent"))
        )
        return await call_next(request)

    # added last = outermost: KeepPeer sees the TCP peer, then X-Forwarded-For / -Proto
    # apply from QUEUELENS_TRUSTED_PROXIES only (the image runs uvicorn --no-proxy-headers)
    app.add_middleware(
        ProxyHeadersMiddleware, trusted_hosts=app.state.settings.trusted_proxy_list
    )
    app.add_middleware(KeepPeer)

    from fastapi import Depends
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html

    from app.auth.basic import get_current_username

    @app.get("/openapi.json", include_in_schema=False)
    async def _openapi(_user: str = Depends(get_current_username)) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    async def _docs(_user: str = Depends(get_current_username)) -> Response:
        return get_swagger_ui_html(openapi_url="/openapi.json", title="QueueLens API")

    @app.get("/redoc", include_in_schema=False)
    async def _redoc(_user: str = Depends(get_current_username)) -> Response:
        return get_redoc_html(openapi_url="/openapi.json", title="QueueLens API")

    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(queues.router)
    app.include_router(audit.router)
    app.include_router(messages.router)
    app.include_router(actions.router)
    app.include_router(bulk.router)
    app.include_router(platform.router)
    app.include_router(policies.router)
    app.include_router(web.router)
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "web" / "static"),
        name="static",
    )
    return app


app = create_app()
