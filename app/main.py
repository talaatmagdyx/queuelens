from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import actions, audit, health, messages, queues
from app.application.action_service import ActionService
from app.application.message_service import MessageService
from app.application.queue_service import QueueService
from app.config import Settings, get_settings
from app.infrastructure.persistence.audit_repository import AuditRepository
from app.infrastructure.persistence.database import Database
from app.infrastructure.rabbitmq.connection import RabbitMQConnection
from app.infrastructure.rabbitmq.management_client import RabbitMQManagementClient
from app.infrastructure.rabbitmq.message_browser import MessageBrowser
from app.infrastructure.rabbitmq.message_operator import MessageOperator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.management_client.start()
    await app.state.database.start()
    await app.state.rabbitmq_connection.start()
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        await app.state.management_client.close()
        await app.state.rabbitmq_connection.close()
        await app.state.database.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="QueueLens", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    database = Database(app.state.settings.database_url)
    app.state.database = database
    app.state.audit_repository = AuditRepository(database)
    rabbitmq_connection = RabbitMQConnection(app.state.settings)
    app.state.rabbitmq_connection = rabbitmq_connection
    app.state.message_service = MessageService(MessageBrowser(rabbitmq_connection))
    app.state.action_service = ActionService(
        app.state.settings, MessageOperator(rabbitmq_connection)
    )
    management = RabbitMQManagementClient(app.state.settings)
    app.state.management_client = management
    app.state.queue_service = QueueService(management)
    app.include_router(health.router)
    app.include_router(queues.router)
    app.include_router(audit.router)
    app.include_router(messages.router)
    app.include_router(actions.router)
    return app


app = create_app()
