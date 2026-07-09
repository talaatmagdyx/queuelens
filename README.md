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
```

## API safety contract

- Browsing uses `basic_get` with requeue behavior and never removes messages.
- Copy replay publishes and requeues the original.
- Move replay publishes first and acknowledges the original only after publish succeeds.
- Park publishes to `{source_queue}.parking` and acknowledges only after publish succeeds.
- Delete acknowledges only after explicit confirmation.
- Actions write an audit-start event before execution and a success/failure event after execution.

## Development

```bash
python -m pip install '.[dev]'
ruff check app tests
pytest -q
mypy app
```
