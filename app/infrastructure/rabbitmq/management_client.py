from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from urllib.parse import quote

import httpx

from app.config import Settings


class RabbitMQManagementError(RuntimeError):
    """Raised when the RabbitMQ Management API returns an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RabbitMQManagementClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.rabbitmq_management_url.rstrip("/"),
                auth=httpx.BasicAuth(
                    self._settings.rabbitmq_management_username,
                    self._settings.rabbitmq_management_password,
                ),
                timeout=self._settings.rabbitmq_operation_timeout_seconds,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("RabbitMQ Management client has not started")
        return self._client

    async def list_queues(self) -> list[dict[str, Any]]:
        vhost = quote(self._settings.rabbitmq_vhost, safe="")
        response = await self.client.get(f"/api/queues/{vhost}")
        return cast(list[dict[str, Any]], await self._json_or_raise(response))

    async def get_queue(self, queue_name: str) -> dict[str, Any]:
        vhost = quote(self._settings.rabbitmq_vhost, safe="")
        encoded_queue = quote(queue_name, safe="")
        path = f"/api/queues/{vhost}/{encoded_queue}"
        response = await self.client.get(path)
        return cast(dict[str, Any], await self._json_or_raise(response))

    async def _json_or_raise(self, response: httpx.Response) -> Any:
        if response.is_error:
            raise RabbitMQManagementError(
                f"RabbitMQ Management API returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()


@asynccontextmanager
async def management_client(settings: Settings) -> AsyncIterator[RabbitMQManagementClient]:
    client = RabbitMQManagementClient(settings)
    await client.start()
    try:
        yield client
    finally:
        await client.close()
