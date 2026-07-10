from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from aiormq.exceptions import ChannelNotFoundEntity
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import actions, audit, bulk, health, messages, metrics, queues
from app.application.action_service import ActionService
from app.application.bulk_service import BulkActionService
from app.application.message_service import MessageService
from app.application.queue_service import QueueService
from app.config import Settings, get_settings
from app.infrastructure.persistence.audit_repository import AuditRepository
from app.infrastructure.persistence.database import Database
from app.infrastructure.rabbitmq.connection import RabbitMQConnection, RabbitMQUnavailableError
from app.infrastructure.rabbitmq.management_client import (
    RabbitMQManagementClient,
    RabbitMQManagementError,
)
from app.infrastructure.rabbitmq.message_browser import MessageBrowser
from app.infrastructure.rabbitmq.message_operator import MessageOperator
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.management_client.start()
    try:
        await app.state.database.start()
        await app.state.rabbitmq_connection.start()
        app.state.rabbitmq_connection.start_reconnect_loop()
        app.state.ready = True
        yield
    finally:
        app.state.ready = False
        await app.state.management_client.close()
        await app.state.rabbitmq_connection.close()
        await app.state.database.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="QueueLens", version="0.4.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    database = Database(app.state.settings.database_url)
    app.state.database = database
    app.state.audit_repository = AuditRepository(database)
    rabbitmq_connection = RabbitMQConnection(app.state.settings)
    app.state.rabbitmq_connection = rabbitmq_connection
    browser = MessageBrowser(rabbitmq_connection)
    operator = MessageOperator(rabbitmq_connection)
    app.state.message_service = MessageService(browser)
    app.state.action_service = ActionService(app.state.settings, operator)
    app.state.bulk_service = BulkActionService(app.state.settings, browser, operator)
    management = RabbitMQManagementClient(app.state.settings)
    app.state.management_client = management
    app.state.queue_service = QueueService(management)
    # base.html renders the environment badge and sidebar identity on every page
    web.templates.env.globals["app_environment"] = app.state.settings.environment
    web.templates.env.globals["admin_username"] = app.state.settings.admin_username
    _register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(queues.router)
    app.include_router(audit.router)
    app.include_router(messages.router)
    app.include_router(actions.router)
    app.include_router(bulk.router)
    app.include_router(web.router)
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "web" / "static"),
        name="static",
    )
    return app


app = create_app()
