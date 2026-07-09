from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
from aio_pika.abc import AbstractRobustConnection

from app.config import Settings


class RabbitMQConnection:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connection: AbstractRobustConnection | None = None

    async def start(self) -> None:
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(
                self._settings.rabbitmq_url,
                client_properties={"connection_name": self._settings.rabbitmq_connection_name},
                timeout=self._settings.rabbitmq_operation_timeout_seconds,
            )

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._connection = None

    @property
    def connection(self) -> AbstractRobustConnection:
        if self._connection is None or self._connection.is_closed:
            raise RuntimeError("RabbitMQ connection is not available")
        return self._connection

    @asynccontextmanager
    async def channel(self) -> AsyncIterator[aio_pika.abc.AbstractChannel]:
        channel = await self.connection.channel()
        try:
            yield channel
        finally:
            await channel.close()

