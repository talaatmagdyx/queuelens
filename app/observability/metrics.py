"""Prometheus metrics. Counters are updated where actions execute; the gauges are
refreshed at scrape time so they reflect the broker (and the database) right now."""

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

ALERT_DELIVERIES = Counter(
    "queuelens_alert_deliveries_total",
    "Alert notification deliveries by channel and result (ok | failed | skipped)",
    ["channel", "result"],
)

POLICY_RUNS = Counter(
    "queuelens_policy_runs_total",
    "Replay policy runs by policy and result (idle | success | partial)",
    ["policy", "result"],
)

POLICY_MESSAGES = Counter(
    "queuelens_policy_messages_total",
    "Messages replay policies acted on, by policy and outcome "
    "(replayed | parked | failed | held: no consumers)",
    ["policy", "outcome"],
)

POLICY_PAUSED = Gauge(
    "queuelens_policy_paused",
    "1 while a replay policy has paused itself after failed runs, else 0",
    ["policy"],
)
