from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, queues
from app.application.queue_service import QueueService
from app.config import Settings, get_settings
from app.infrastructure.rabbitmq.management_client import RabbitMQManagementClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.management_client.start()
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        await app.state.management_client.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="QueueLens", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    management = RabbitMQManagementClient(app.state.settings)
    app.state.management_client = management
    app.state.queue_service = QueueService(management)
    app.include_router(health.router)
    app.include_router(queues.router)
    return app


app = create_app()
