import httpx
import pytest

from app.config import Settings
from app.domain.models import ReplayTarget
from app.infrastructure.persistence.audit_repository import AuditRepository
from app.infrastructure.rabbitmq.message_browser import MessageBrowser
from app.infrastructure.rabbitmq.message_operator import MessageOperator
from app.main import create_app


class ActionMessage:
    body = b'{"request_id":"req-1"}'
    headers = {}
    timestamp = None
    message_id = "message-1"
    content_type = "application/json"
    content_encoding = None
    delivery_mode = 2
    priority = 0
    correlation_id = None
    reply_to = None
    expiration = None
    type = "order.created"
    user_id = None
    app_id = "orders"
    exchange = "orders.exchange"
    routing_key = "orders.created"
    redelivered = False
    processed = False

    def __init__(self) -> None:
        self.acked = False
        self.nacked = False

    async def ack(self) -> None:
        self.acked = True
        self.processed = True

    async def nack(self, requeue: bool = False) -> None:
        self.nacked = requeue
        self.processed = True


class FakeExchange:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.published: list[tuple[object, str]] = []

    async def publish(self, message: object, routing_key: str) -> None:
        if self.should_fail:
            raise RuntimeError("publish failed")
        self.published.append((message, routing_key))


class ActionChannel:
    def __init__(self, messages: list[ActionMessage], exchange: FakeExchange) -> None:
        self.messages = messages
        self.default_exchange = exchange

    async def basic_get(self, _queue: str, no_ack: bool = False) -> ActionMessage | None:
        assert no_ack is False
        return self.messages.pop(0) if self.messages else None

    async def get_exchange(self, _name: str, ensure: bool = False) -> FakeExchange:
        assert ensure is False
        return self.default_exchange

    async def close(self) -> None:
        return None


class ChannelContext:
    def __init__(self, channel: ActionChannel) -> None:
        self.channel = channel

    async def __aenter__(self) -> ActionChannel:
        return self.channel

    async def __aexit__(self, *_args: object) -> None:
        return None


class ActionConnection:
    def __init__(self, context: ChannelContext) -> None:
        self.context = context

    def channel(self) -> ChannelContext:
        return self.context


@pytest.mark.asyncio
async def test_move_publishes_before_ack_and_requeues_other_messages() -> None:
    first = ActionMessage()
    target = ActionMessage()
    target.message_id = "message-2"
    exchange = FakeExchange()
    connection = ActionConnection(ChannelContext(ActionChannel([first, target], exchange)))
    operator = MessageOperator(connection)  # type: ignore[arg-type]
    fingerprint = MessageBrowser._to_record("orders.dlq", target).fingerprint

    result = await operator.operate(
        source_queue="orders.dlq",
        fingerprint=fingerprint,
        action="move",
        target=ReplayTarget(type="queue", queue="orders"),
    )

    assert result["status"] == "success"
    assert first.acked is False
    assert first.nacked is True
    assert target.acked is True
    assert target.nacked is False
    assert len(exchange.published) == 1


@pytest.mark.asyncio
async def test_publish_failure_requeues_target_and_does_not_ack() -> None:
    target = ActionMessage()
    exchange = FakeExchange(should_fail=True)
    connection = ActionConnection(ChannelContext(ActionChannel([target], exchange)))
    operator = MessageOperator(connection)  # type: ignore[arg-type]
    fingerprint = MessageBrowser._to_record("orders.dlq", target).fingerprint

    with pytest.raises(RuntimeError, match="publish failed"):
        await operator.operate(
            source_queue="orders.dlq",
            fingerprint=fingerprint,
            action="move",
            target=ReplayTarget(type="queue", queue="orders"),
        )

    assert target.acked is False
    assert target.nacked is True


@pytest.mark.asyncio
async def test_replay_route_requires_confirmation_and_audits_success(tmp_path) -> None:
    settings = Settings(
        auth_enabled=False,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/actions.db",
    )
    app = create_app(settings)
    await app.state.database.start()

    class FakeActionService:
        async def replay(self, **_kwargs: object) -> dict[str, object]:
            return {"status": "success", "action": "copy"}

    app.state.action_service = FakeActionService()
    body = {
        "source_queue": "orders.dlq",
        "fingerprint": "a" * 64,
        "mode": "copy",
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_confirmation = await client.post("/api/messages/replay", json=body)
        body["confirm"] = True
        successful = await client.post("/api/messages/replay", json=body)

    events = await AuditRepository(app.state.database).list(action="replay")
    await app.state.database.close()

    assert missing_confirmation.status_code == 400
    assert successful.status_code == 200
    assert [event["result"] for event in events] == ["success", "started"]
