# QueueLens

Async RabbitMQ DLQ inspector with safe replay.

## Why QueueLens?

RabbitMQ dead-letter queues are easy to create but painful to operate. During an incident,
engineers need to inspect failed messages, understand their `x-death` history, and then
replay, park, or delete them — usually with risky one-off scripts that can lose data.

QueueLens gives backend teams a safe UI and API for exactly that flow:

```text
inspect DLQ safely -> understand the message -> replay / park / delete safely -> audit everything
```

## Features

- **DLQ auto-detection** — by name convention (`.dlq`, `_dlq`, `dead`) or by being the queue
  another queue dead-letters into
- **Non-destructive browsing** — preview messages without consuming them
- **Message inspection** — payload (JSON / text / base64), headers, properties, routing data,
  and parsed `x-death` history
- **Copy & move replay** — to a queue or exchange + routing key, per action or preconfigured,
  with `x-queuelens-*` provenance headers stamped on every replayed message
- **Park & delete** — park moves a message to `{queue}.parking` (created on demand);
  both require explicit confirmation
- **Audit log** — every action writes an attempt event before execution and an outcome event after
- **Honest failure modes** — friendly 404 for unknown queues and ambiguous messages, 400 for
  unroutable targets, 503 while the broker is down, and a `/ready` probe that reports real
  broker connectivity

## Safety guarantees

- Browsing uses non-destructive preview with requeue.
- Copy replay keeps the source DLQ message.
- Move replay publishes first and removes the original only after publish succeeds.
- Park publishes first and removes the original only after publish succeeds.
- Delete requires explicit confirmation.
- Queue replay targets are verified to exist before publishing, and publishes are mandatory
  with returned-message errors enabled — a failed or unroutable publish never removes the
  original DLQ message.
- Every action writes audit attempt and outcome events; if the attempt cannot be persisted,
  the action is rejected.
- Detail lookup and mutation use a bounded re-fetch window and fail safely when a message
  fingerprint matches zero or multiple messages.

## Quick start

```bash
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000). The default local credentials are
`admin` / `change-me`; change them before using a shared environment.

RabbitMQ Management UI is available at [http://localhost:15672](http://localhost:15672)
with `queuelens` / `queuelens`.

## Screenshots

**DLQ dashboard** — source queues with dead-letter config are not misreported as DLQs:

![Dashboard](docs/screenshots/dashboard.png)

**Queue view** — non-destructive preview with payload format and size:

![Queue detail](docs/screenshots/queue.png)

**Message detail** — payload, headers, and parsed `x-death` history:

![Message detail](docs/screenshots/message-detail.png)

![x-death](docs/screenshots/x-death.png)

**Actions** — replay target per action, with a clear result and audit link:

![Action result](docs/screenshots/action-result.png)

**Audit log** — attempt and outcome events for every action:

![Audit log](docs/screenshots/audit.png)

## Configuration

All external I/O is asynchronous (`aio-pika`, `httpx.AsyncClient`, SQLAlchemy asyncio with
`aiosqlite`). Important environment variables:

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

Preconfigured replay targets can be supplied through `QUEUELENS_REPLAY_TARGETS_JSON`; see
[`config/replay-targets.example.json`](config/replay-targets.example.json). A target can also
be entered per action in the message detail UI.

> **Sensitive data warning:** Phase 1 does not include sensitive-field masking. Do not expose
> QueueLens publicly; run it inside a trusted private network or behind secure internal access
> controls because DLQ payloads may contain tokens, emails, customer data, or internal
> identifiers.

## Known limitations (Phase 1)

- Single-message actions only — bulk operations are deferred
- Preview capped at `QUEUELENS_MAX_PREVIEW_MESSAGES` messages
- HTTP Basic Auth only
- SQLite audit store (PostgreSQL deferred for teams needing stronger concurrency and retention)
- No sensitive-field masking yet
- Message fingerprints are best-effort identifiers for the current preview batch, bounded
  re-fetch matching, and audit correlation; they are not globally stable RabbitMQ message IDs.
  Mutating actions fail safely unless exactly one matching message is found.

## Roadmap

1. Sensitive-field masking (display-only)
2. Preview-limit honesty and pagination
3. Bulk operations (dry-run preview, per-message results, partial-failure summary)
4. PostgreSQL audit store, alerts, metrics, RBAC

## Development

```bash
python -m pip install '.[dev]'
ruff check app tests
pytest -q
mypy app
```

`tests/test_integration_rabbitmq.py` runs the browse → park → replay → delete flow against a
real broker and is skipped automatically when none is reachable. To include it:

```bash
docker compose up -d rabbitmq
pytest -q   # picks it up once the broker is reachable
```

Point it at another broker with `QUEUELENS_IT_AMQP_URL` and `QUEUELENS_IT_MANAGEMENT_URL`.

CI (GitHub Actions) runs lint, type-checks, the full test suite against a real RabbitMQ
service container, and a Docker image build on every push.
