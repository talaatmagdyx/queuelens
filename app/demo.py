"""Demo data for the bundled broker: `python -m app.demo` (docker compose runs it once).

Every message is dead-lettered for real — published to a work queue and rejected, so its
x-death history is the broker's own, not a hand-written header. Messages that died more
than once went round the work queue that many times: RabbitMQ 3.x adds to the x-death a
republished message carries, so they show 3 or 5 deaths; 4.x starts the count afresh for a
republished copy, so there every message shows one. Safe to run again: it does nothing
when the demo DLQs already hold messages.
"""

import asyncio
import gzip
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika

from app.config import get_settings

TENANTS = ("acme", "globex", "initech", "umbrella")
PAYMENT_ERRORS = (
    ("card_declined", "Your card was declined."),
    ("insufficient_funds", "The card has insufficient funds to complete the purchase."),
    ("gateway_timeout", "Payment provider did not respond within 30s."),
    ("fraud_suspected", "Blocked by risk rules: velocity limit exceeded."),
)


def _payment(i: int) -> dict[str, Any]:
    code, message = PAYMENT_ERRORS[i % len(PAYMENT_ERRORS)]
    return {
        "payment_id": f"pay_{8_400_000 + i * 37:x}",
        "order_id": f"ord_{10_400 + i}",
        "amount": {"value": f"{19 + (i * 7) % 480}.{(i * 13) % 100:02d}", "currency": "EUR"},
        "provider": ("stripe", "adyen")[i % 2],
        "error": {"code": code, "message": message},
    }


def _order(i: int) -> dict[str, Any]:
    return {
        "order_id": f"ord_{20_100 + i}",
        "customer_id": f"cus_{3_000 + i * 11}",
        "items": [{"sku": f"SKU-{4_400 + i % 9}", "qty": 1 + i % 3}],
        "error": "inventory reservation timed out after 3 attempts",
    }


# (work queue, DLQ, exchange, routing key, count, payload, extra queue arguments for the DLQ)
QUEUES: tuple[tuple[str, str, str, str, int, Any, dict[str, Any]], ...] = (
    ("payments.retry", "payments.retry.dlq", "payments", "payment.retry", 220, _payment, {}),
    ("orders.created", "orders.created.dlq", "orders", "order.created", 37, _order, {}),
    ("inventory.sync", "inventory.sync.dlq", "inventory", "inventory.sync", 9,
     lambda i: f"SKU-{4_400 + i},warehouse=AMS-{1 + i % 3},delta=-{1 + i % 4}", {}),
    ("notifications.email", "notifications.email.dlq", "notifications", "email.send", 4,
     lambda i: {"template": "order_confirmation", "to": f"customer{i}@example.com",
                "error": "SMTP 451 4.7.1 greylisted, try again later"}, {}),
    # a quorum DLQ with a delivery limit: QueueLens badges it "not browsable"
    ("ledger.events", "ledger.events.dlq", "ledger", "ledger.post", 6,
     lambda i: {"entry_id": f"led_{900 + i}", "error": "unbalanced journal entry"},
     {"x-queue-type": "quorum", "x-delivery-limit": 5}),
)


def _deaths(i: int) -> int:
    return 5 if i % 25 == 3 else 3 if i % 9 == 4 else 1


def _message(work: str, i: int, payload: Any, now: datetime) -> aio_pika.Message:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    body, encoding = text.encode(), None
    if work == "payments.retry" and i % 7 == 5:  # some producers compress
        body, encoding = gzip.compress(body), "gzip"
    return aio_pika.Message(
        body,
        content_type="text/plain" if isinstance(payload, str) else "application/json",
        content_encoding=encoding,
        message_id=f"{work.split('.')[0][:3]}-{i:04d}",
        correlation_id=f"req-{(i * 7919) % 100_000:05d}",
        timestamp=now - timedelta(minutes=3 * i),
        app_id=f"{work.split('.')[0]}-svc",
        type=f"{work}.failed",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        headers={"x-tenant": TENANTS[i % len(TENANTS)], "x-trace-id": f"{i * 2654435761:x}"[:16]},
    )


PROPERTIES = ("content_type", "content_encoding", "message_id", "correlation_id",
              "timestamp", "app_id", "type")


def _copy(incoming: Any) -> aio_pika.Message:
    """The message as it came back from the DLQ, x-death included, ready to go round again."""
    return aio_pika.Message(
        incoming.body, headers=dict(incoming.headers or {}),
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        **{name: getattr(incoming, name) for name in PROPERTIES},
    )


async def _next(queue: Any) -> Any:
    """basic.get, waiting briefly: a reject has no reply, so its dead-letter lands a moment
    later."""
    for _ in range(100):
        message = await queue.get(no_ack=False, fail=False)
        if message is not None:
            return message
        await asyncio.sleep(0.02)
    raise TimeoutError(f"nothing arrived in {queue.name}")


async def _dead_letter(exchange: Any, work: Any, routing_key: str,
                       message: aio_pika.Message) -> None:
    await exchange.publish(message, routing_key=routing_key)
    got = await _next(work)
    await got.reject(requeue=False)  # the broker dead-letters it, stamping x-death


async def seed(url: str, prefix: str = "") -> bool:
    """Seed the demo topology and dead-letters. False when it was already there."""
    connection = await aio_pika.connect(url)
    now = datetime.now(UTC)
    try:
        channel = await connection.channel()
        for work_name, dlq_name, exchange_name, routing_key, count, payload, dlq_args in QUEUES:
            work_name, dlq_name = prefix + work_name, prefix + dlq_name
            dlq = await channel.declare_queue(dlq_name, durable=True, arguments=dlq_args or None)
            if (dlq.declaration_result.message_count or 0) > 0:
                return False  # already seeded
            exchange = await channel.declare_exchange(
                prefix + exchange_name, aio_pika.ExchangeType.DIRECT, durable=True
            )
            work = await channel.declare_queue(work_name, durable=True, arguments={
                "x-dead-letter-exchange": "", "x-dead-letter-routing-key": dlq_name,
            })
            await work.bind(exchange, routing_key=routing_key)
            # messages that died several times go round the work queue first, while the
            # DLQ holds nothing else, then wait aside until their turn in the final order
            held: dict[int, aio_pika.Message] = {}
            for i in (i for i in range(count) if _deaths(i) > 1):
                message = _message(work_name.removeprefix(prefix), i, payload(i), now)
                for _ in range(_deaths(i)):
                    await _dead_letter(exchange, work, routing_key, message)
                    back = await _next(dlq)
                    message = _copy(back)
                    await back.ack()
                held[i] = message
            for i in range(count):
                if i in held:  # its x-death is the broker's, from the rounds above
                    await channel.default_exchange.publish(held[i], routing_key=dlq_name)
                else:
                    message = _message(work_name.removeprefix(prefix), i, payload(i), now)
                    await _dead_letter(exchange, work, routing_key, message)
        return True
    finally:
        await connection.close()


if __name__ == "__main__":
    seeded = asyncio.run(seed(get_settings().rabbitmq_url))
    print("QueueLens demo: dead-letter queues seeded" if seeded else
          "QueueLens demo: already seeded, nothing to do")
