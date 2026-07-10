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
- **Sensitive-field masking** — values under configurable keys (`password`, `token`, `email`, …)
  render as `•••`; display-only, replay payloads are never modified
- **Audit log** — every action writes an attempt event before execution and an outcome event after
- **Preview honesty** — the queue view says "showing 100 of 4,812" instead of pretending you saw everything
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

## Documentation

| Doc | What's inside |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering, components, request flows, design decisions (fingerprints, publish-before-ack, health tracking) |
| [docs/API.md](docs/API.md) | Full REST API reference: endpoints, request/response shapes, error matrix |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every `QUEUELENS_*` environment variable, replay-target format, broker permissions |
| [docs/SAFETY.md](docs/SAFETY.md) | The safety model: each guarantee, how it's enforced, the failure matrix, known limits |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deployment, security posture, health probes, audit store, troubleshooting |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, test strategy, CI, code conventions, release checklist |

Interactive OpenAPI docs are served by the app itself at `/docs`.

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
`aiosqlite`). Everything is configured through `QUEUELENS_*` environment variables — broker
URLs, credentials, preview/scan limits, and preconfigured replay targets. See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full reference and
[`config/replay-targets.example.json`](config/replay-targets.example.json) for the replay
target format. A replay target can also be entered per action in the message detail UI.

> **Sensitive data warning:** masking is key-based and display-only — secrets under
> unlisted keys are shown, and replayed messages carry the original payload. Do not expose
> QueueLens publicly; run it inside a trusted private network or behind secure internal
> access controls.

## Known limitations (Phase 1)

- Single-message actions only — bulk operations are deferred
- Preview capped at `QUEUELENS_MAX_PREVIEW_MESSAGES` messages (the UI says so when it happens)
- HTTP Basic Auth only
- SQLite audit store (PostgreSQL deferred for teams needing stronger concurrency and retention)
- Masking is key-based and display-only — it does not detect secrets under unlisted keys
- Message fingerprints are best-effort identifiers for the current preview batch, bounded
  re-fetch matching, and audit correlation; they are not globally stable RabbitMQ message IDs.
  Mutating actions fail safely unless exactly one matching message is found.

## Roadmap

1. Bulk operations (dry-run preview, per-message results, partial-failure summary)
2. Full pagination beyond the preview window
3. PostgreSQL audit store, alerts, metrics, RBAC

## Development

```bash
python -m pip install '.[dev]'
ruff check app tests && mypy app && pytest -q
```

`tests/test_integration_rabbitmq.py` runs the browse → park → replay → delete flow against a
real broker (`docker compose up -d rabbitmq`) and is skipped automatically when none is
reachable. CI runs lint, strict type-checks, the full suite against a real RabbitMQ service
container, and a Docker image build on every push.

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the test strategy, code conventions, and
the release checklist.

## License

MIT — see [LICENSE](LICENSE).
