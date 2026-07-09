import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
from aio_pika.abc import AbstractRobustConnection

from app.config import Settings


class RabbitMQConnection:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connection: AbstractRobustConnection | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        self._started = True
        if self._connection is not None and not self._connection.is_closed:
            return
        try:
            self._connection = await aio_pika.connect_robust(
                self._settings.rabbitmq_url,
                client_properties={"connection_name": self._settings.rabbitmq_connection_name},
                timeout=self._settings.rabbitmq_operation_timeout_seconds,
            )
        except Exception:
            self._connection = None

    def start_reconnect_loop(self) -> None:
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while self._started:
            if not self.is_connected:
                await self.start()
            await asyncio.sleep(5)

    async def close(self) -> None:
        self._started = False
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            await asyncio.gather(self._reconnect_task, return_exceptions=True)
            self._reconnect_task = None
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._connection = None

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def is_connected(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

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
