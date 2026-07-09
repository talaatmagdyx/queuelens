from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api.routes import health
from app.config import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ready = True
    yield
    app.state.ready = False


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="QueueLens", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.include_router(health.router)
    return app


app = create_app()

