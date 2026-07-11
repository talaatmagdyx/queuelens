# Architecture

QueueLens is a single FastAPI application with a strict layering rule: **web and API routes
never touch RabbitMQ or the database directly** — they go through application services, which
go through infrastructure adapters.

```text
app/
  web/routes.py            SPA entry (/app) + legacy redirects          ┐
  api/routes/*.py          JSON API                     ├─ presentation
  auth/basic.py            HTTP Basic Auth dependency   ┘
  application/
    queue_service.py       queue listing + DLQ detection
    message_service.py     browsing, detail lookup, serialization
    action_service.py      replay / park / delete orchestration
  infrastructure/
    rabbitmq/connection.py        robust AMQP connection + health tracking
    rabbitmq/management_client.py async RabbitMQ Management API client (httpx)
    rabbitmq/message_browser.py   non-destructive preview (basic_get + requeue)
    rabbitmq/message_operator.py  mutating actions (publish-before-ack)
    persistence/                  SQLAlchemy asyncio + SQLite audit store
  domain/
    models.py              frozen dataclasses (QueueInfo, MessageRecord, AuditEntry, ReplayTarget)
    fingerprint.py         best-effort message identity
    xdeath.py              x-death header normalization
  config.py                pydantic-settings (QUEUELENS_* env vars)
  main.py                  app factory, lifespan, error handlers
```

## Component responsibilities

### Two RabbitMQ access paths

| Path | Used for | Why |
|---|---|---|
| **Management HTTP API** (`management_client.py`) | Queue discovery, queue stats | Queue listing is not possible over AMQP |
| **AMQP** (`connection.py` + browser/operator) | Message browsing and all mutations | Only AMQP gives per-message ack/nack control |

Both are created once in `create_app` and stored on `app.state`; routes resolve services from
`request.app.state`, which is also what makes every collaborator swappable in tests.

### Connection management (`RabbitMQConnection`)

- `aio_pika.connect_robust` with a background retry loop (every 5 s) for the initial connect.
- **Health tracking**: `RobustConnection.is_closed` stays `False` while aio-pika reconnects, so
  liveness is tracked through `close_callbacks` / `reconnect_callbacks` instead. `/ready`
  reports 503 the moment the broker connection drops.
- Channels are opened per operation with `on_return_raises=True`, so a mandatory publish that
  the broker cannot route raises `DeliveryError` instead of silently dropping the message.
  This is a load-bearing safety property — see [SAFETY.md](SAFETY.md).

### Message identity (`domain/fingerprint.py`)

RabbitMQ has no stable, addressable message ID, so QueueLens derives a SHA-256 fingerprint
from `(queue, body hash, headers, message_id, timestamp, exchange, routing_key)`.

Consequences, by design:

- Fingerprints are **stable across requeues** (the `redelivered` flag is excluded).
- Two byte-identical messages share a fingerprint. Detail lookup and every mutating action
  re-scan a bounded window (`QUEUELENS_REFETCH_WINDOW_SIZE`) and **refuse to act unless exactly
  one message matches** (HTTP 409 otherwise).
- Fingerprints are correlation IDs for the current preview batch and the audit log — not
  globally stable identifiers.

### Mutating actions (`MessageOperator.operate`)

One code path drives all four actions:

1. Passively declare the source queue (missing queue → 404, nothing consumed).
2. `basic_get` up to `max_scan` messages, computing fingerprints as they arrive.
3. Require exactly one match, else raise `LookupError` (→ 409) and requeue everything.
4. For publish actions, resolve the target first: park **declares** its durable
   `{queue}.parking` queue; replay queue targets are **passively verified** to exist.
5. Publish (mandatory, confirms on), and only then ack the original. Copy nacks it back.
6. Requeue every other scanned message, newest first.
7. Any failure at any step requeues all unacked messages; if the channel already died, the
   broker requeues them itself (`ChannelInvalidStateError` is tolerated).

### Audit (`AuditRepository`)

Every action writes a `started` event **before** execution and a `success`/`failed` event
after. If the attempt event cannot be persisted, the action is rejected — audit is a
precondition, not a best effort. Store is SQLite via SQLAlchemy asyncio (`aiosqlite`);
the schema is created at startup (`Base.metadata.create_all`), no migrations in Phase 1.

### DLQ detection (`QueueService`)

A queue is a DLQ when its **name** matches a convention (`.dlq`, `_dlq`, `dead`) or when
**another queue dead-letters into it** via the default exchange (its name appears as some
queue's `x-dead-letter-routing-key` with an empty `x-dead-letter-exchange`). A queue that
merely *declares* `x-dead-letter-*` arguments is a source, not a DLQ, and is excluded.

### Error handling (`main.py`)

Domain and infrastructure exceptions are mapped centrally; responses are JSON under `/api/*`
and a rendered error page elsewhere. See [API.md](API.md#errors) for the full matrix.

## Request flows

**Browse** — `GET /queues/{name}` → `MessageService.list_messages` → `MessageBrowser`:
`basic_get` up to the preview cap, build `MessageRecord`s, then nack-requeue everything in a
`finally` block. Browsing can never consume a message (it does set the broker's `redelivered`
flag — unavoidable with `basic_get`).

**Replay** — `POST /api/messages/replay` → audit `started` → `ActionService.replay` resolves
the target (request body target wins over `QUEUELENS_REPLAY_TARGETS_JSON`), stamps
`x-queuelens-*` provenance headers → `MessageOperator.operate` → audit outcome → result.

## Testing strategy

- **Unit/route tests** (`tests/test_*.py`) swap services on `app.state` and use in-memory
  fakes for channels/queues — fast, no broker.
- **`tests/test_integration_rabbitmq.py`** runs the full journey against a real broker
  (auto-skipped when unreachable). It exists because the fakes once encoded the same wrong
  assumptions as the code — see the module docstring. CI always runs it via a RabbitMQ
  service container.
