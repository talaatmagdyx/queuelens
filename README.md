# QueueLens

QueueLens is an async RabbitMQ DLQ inspector and safe replay tool.

## Phase 1

Phase 1 supports one RabbitMQ cluster with:

- Basic Auth
- Queue and DLQ discovery
- Safe message browsing and detail inspection
- JSON and `x-death` parsing
- Copy replay and move replay
- Parking and deleting one message at a time
- Async SQLite audit logging
- Docker Compose deployment

Bulk operations, masking, alerts, metrics, PostgreSQL, Kubernetes, RBAC, and message indexing are intentionally deferred.

Message fingerprints are best-effort identifiers for the current preview batch, bounded re-fetch matching, and audit correlation; they are not globally stable RabbitMQ message IDs. Mutating actions fail safely unless exactly one matching message is found.

## Run locally

```bash
docker compose up --build
```

Open [http://localhost:8000/login](http://localhost:8000/login). The default local credentials are `admin` / `change-me`; change them before using a shared environment.

RabbitMQ Management UI is available at [http://localhost:15672](http://localhost:15672) with `queuelens` / `queuelens`.

## Configuration

All external I/O is asynchronous. The application uses `aio-pika`, `httpx.AsyncClient`, and SQLAlchemy asyncio with `aiosqlite`.

Replay targets can be supplied through `QUEUELENS_REPLAY_TARGETS_JSON`; see [`config/replay-targets.example.json`](config/replay-targets.example.json).

Important environment variables:

```env
QUEUELENS_AUTH_ENABLED=true
QUEUELENS_ADMIN_USERNAME=admin
QUEUELENS_ADMIN_PASSWORD=change-me
QUEUELENS_RABBITMQ_URL=amqp://queuelens:queuelens@localhost:5672/
QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://localhost:15672
QUEUELENS_DATABASE_URL=sqlite+aiosqlite:///./data/queuelens.db
QUEUELENS_MAX_PREVIEW_MESSAGES=100
QUEUELENS_MAX_MESSAGE_SIZE_BYTES=1048576
QUEUELENS_OPERATION_TIMEOUT_SECONDS=10
QUEUELENS_REFETCH_WINDOW_SIZE=100
```

> **Sensitive data warning:** Phase 1 does not include sensitive-field masking. Do not expose QueueLens publicly; run it inside a trusted private network or behind secure internal access controls because DLQ payloads may contain tokens, emails, customer data, or internal identifiers.

## API safety contract

- Browsing uses `basic_get` with requeue behavior and never removes messages.
- Copy replay publishes and requeues the original.
- Move replay publishes first and acknowledges the original only after publish succeeds.
- Park declares `{source_queue}.parking` (durable, created on demand) and acknowledges only
  after publish succeeds.
- Queue replay targets are verified to exist before publishing, and publishes are mandatory
  with returned-message errors enabled, so an unroutable publish fails the action instead of
  silently dropping the message.
- A replay target can be supplied per action in the message detail UI (queue, or
  exchange + routing key); otherwise the configured target for the queue is used.
- Delete acknowledges only after explicit confirmation.
- Actions write an audit-attempt event before execution and a success/failure event after execution; if the attempt cannot be persisted, the action is rejected.
- Detail lookup and mutation use a bounded re-fetch window and fail safely when the fingerprint matches zero or multiple messages.

## Development

```bash
python -m pip install '.[dev]'
ruff check app tests
pytest -q
mypy app
```

SQLite is the default Phase 1 audit store for simple self-hosted deployments. PostgreSQL is deferred for teams that need stronger concurrency, retention, and operational guarantees.
