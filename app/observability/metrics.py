"""Prometheus metrics. Counters are updated where actions execute; the two
gauges are refreshed at scrape time so they reflect the broker right now."""

from prometheus_client import Counter, Gauge, Histogram

RABBITMQ_READY = Gauge(
    "queuelens_rabbitmq_ready",
    "1 when the AMQP connection to RabbitMQ is live, 0 otherwise",
)

DLQ_MESSAGES = Gauge(
    "queuelens_dlq_messages",
    "Messages currently in each detected dead-letter queue",
    ["queue"],
)

PREVIEW_REQUESTS = Counter(
    "queuelens_preview_requests_total",
    "Non-destructive queue previews served (UI and API)",
)

ACTIONS = Counter(
    "queuelens_actions_total",
    "Message actions by action and result; bulk_<action> rows are batch envelopes",
    ["action", "result"],
)

OPERATION_SECONDS = Histogram(
    "queuelens_operation_duration_seconds",
    "Broker operation duration per action",
    ["action"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
