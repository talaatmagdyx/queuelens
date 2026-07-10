# QueueLens

[![CI](https://github.com/talaatmagdyx/queuelens/actions/workflows/ci.yml/badge.svg)](https://github.com/talaatmagdyx/queuelens/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Async RabbitMQ DLQ inspector with safe replay.

## Why QueueLens?

RabbitMQ dead-letter queues are easy to create but painful to operate. During an incident,
engineers need to inspect failed messages, understand their `x-death` history, and then
replay, park, or delete them — usually with risky one-off scripts that can lose data.

QueueLens gives backend teams a safe UI and API for exactly that flow:

```text
inspect DLQ safely -> understand the message -> replay / park / delete safely -> audit everything
```

QueueLens is a **focused RabbitMQ DLQ recovery tool, not a replacement for the RabbitMQ
Management UI** — the Management UI shows queues; QueueLens helps engineers safely recover
failed messages.

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
- **Bulk operations** — replay/park/delete many messages at once, scoped by checkbox
  selection or a payload filter, with a mandatory dry-run first, hard caps, per-message
  results, and duplicates skipped rather than guessed at
- **Sensitive-field masking** — values under configurable keys (`password`, `token`, `email`, …)
  render as `***`; display-only, replay payloads are never modified
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

Local demo with a bundled broker:

```bash
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000). The default local credentials are
`admin` / `change-me`; change them before using a shared environment.
RabbitMQ Management UI is available at [http://localhost:15672](http://localhost:15672)
with `queuelens` / `queuelens`.

## Connect to an existing RabbitMQ cluster

Prebuilt images are published to GHCR on every release:

```bash
docker run --rm -p 8000:8000 \
  -e QUEUELENS_RABBITMQ_URL='amqps://user:pass@rabbitmq.internal:5671/' \
  -e QUEUELENS_RABBITMQ_MANAGEMENT_URL='https://rabbitmq.internal:15671' \
  -e QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME='monitoring-user' \
  -e QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD='…' \
  -e QUEUELENS_ADMIN_USERNAME='admin' \
  -e QUEUELENS_ADMIN_PASSWORD='change-me-now' \
  -v queuelens-data:/app/data \
  ghcr.io/talaatmagdyx/queuelens:latest
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for every variable and the required
broker permissions.

## Production readiness checklist

Before pointing QueueLens at a real cluster:

- [ ] Change the default `QUEUELENS_ADMIN_PASSWORD`.
- [ ] Run behind a VPN, private network, or authenticating reverse proxy — never public.
- [ ] Terminate TLS in front of the app; use `amqps://` / HTTPS Management URLs to the broker.
- [ ] Use a least-privilege RabbitMQ user (read on DLQs, write on replay targets,
      configure only for `*.parking`; Management user needs only the `monitoring` tag).
- [ ] Review `QUEUELENS_MASKED_FIELDS` for your payloads (masking is display-only).
- [ ] Set preview / bulk limits (`QUEUELENS_MAX_PREVIEW_MESSAGES`, `QUEUELENS_MAX_BULK_SIZE`).
- [ ] Persist `/app/data` on a volume and back up the audit database if history matters.
- [ ] Run a single replica — the SQLite audit store and in-memory bulk tokens are
      single-process by design (PostgreSQL backend is on the roadmap).
- [ ] Scrape `/metrics` and load [deploy/prometheus/alerts.yml](deploy/prometheus/alerts.yml).

See [SECURITY.md](SECURITY.md) for the full security model and reporting policy.

## QueueLens vs RabbitMQ Management UI

| Capability | RabbitMQ Management UI | QueueLens |
|---|---|---|
| Queue inspection | ✅ | ✅ |
| DLQ-focused workflow | limited | ✅ |
| Parsed `x-death` history | raw headers | ✅ |
| Safe replay (publish-before-ack) | manual + risky | ✅ |
| Bulk actions with mandatory dry-run | ❌ | ✅ |
| Audit trail (attempt + outcome) | ❌ | ✅ |
| Display masking of sensitive fields | ❌ | ✅ |
| Prometheus metrics for DLQ health | broker-level only | app + DLQ level |

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

**Bulk actions** — dry-run first, then execute on exactly what the dry run counted:

![Bulk actions](docs/screenshots/bulk-actions.png)

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

- Bulk operations act on the scan window (up to `QUEUELENS_MAX_BULK_SIZE` from the head of
  the queue), not the whole queue
- Preview capped at `QUEUELENS_MAX_PREVIEW_MESSAGES` messages (the UI says so when it happens)
- HTTP Basic Auth only
- SQLite audit store (PostgreSQL deferred for teams needing stronger concurrency and retention)
- Masking is key-based and display-only — it does not detect secrets under unlisted keys
- Message fingerprints are best-effort identifiers for the current preview batch, bounded
  re-fetch matching, and audit correlation; they are not globally stable RabbitMQ message IDs.
  Mutating actions fail safely unless exactly one matching message is found.

## Roadmap

1. Full pagination beyond the preview window
2. PostgreSQL audit store, alerts, metrics, RBAC

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
