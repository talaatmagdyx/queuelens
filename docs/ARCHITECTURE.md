# Architecture

QueueLens is a single FastAPI application with a strict layering rule: **web and API routes
never touch RabbitMQ or the database directly**. They go through application services, which
go through infrastructure adapters.

```text
app/
  web/routes.py              SPA entry (/app) + legacy redirects              ┐
  api/routes/*.py            JSON API                                          │
  api/scope.py               per-request environment / vhost (X-QueueLens-*)   ├─ presentation
  auth/basic.py              Basic Auth, roles, failed-login limiter           │
  auth/proxy.py              SSO: identity headers from trusted proxies        ┘
  application/
    environments.py          one broker bundle per (environment, vhost)
    queue_service.py         queue listing, DLQ detection, the browse guard
    message_service.py       browsing, snapshots, detail lookup, serialization
    snapshots.py             one scan kept for paging, search and filters
    action_service.py        replay / park / delete orchestration
    bulk_service.py          bulk dry runs (one-shot tokens) and execution
    bulk_runs.py             a bulk batch run with its full audit trail
    alert_engine.py          in-app alert rules, delivery to channels
    replay_policies.py       automatic retry: plan a run, act through bulk_runs
  infrastructure/
    rabbitmq/connection.py         robust AMQP connection + health tracking
    rabbitmq/management_client.py  async RabbitMQ Management API client (httpx)
    rabbitmq/message_browser.py    non-destructive scans (basic_get + requeue), queue locks
    rabbitmq/message_operator.py   mutating actions (publish-before-ack)
    persistence/database.py        SQLAlchemy asyncio engine, schema, additive migrations
    persistence/store.py           settings, alert rules, users, bulk batches, policies, …
    persistence/audit_repository.py the audit log
    persistence/coordination.py    locks and leadership across replicas (PostgreSQL)
  observability/metrics.py   Prometheus metrics
  domain/                    frozen dataclasses, fingerprints, x-death parsing
  config.py                  pydantic-settings (QUEUELENS_* env vars)
  copy_db.py                 copy every table into another database (SQLite → PostgreSQL)
  demo.py                    real dead-lettered demo data for the bundled compose
  main.py                    app factory, lifespan, middleware, error handlers
deploy/  helm/ (the chart) · prometheus/ (rules + tests) · alertmanager/ (example config)
```

## Component responsibilities

### Two RabbitMQ access paths

| Path | Used for | Why |
|---|---|---|
| **Management HTTP API** (`management_client.py`) | Queue discovery, queue stats, consumer counts | Queue listing is not possible over AMQP |
| **AMQP** (`connection.py` + browser/operator) | Message browsing and all mutations | Only AMQP gives per-message ack/nack control |

### Environments and vhosts, per request (`environments.py`, `api/scope.py`)

There is no instance-wide "active" environment. Every request names its environment and
vhost in `X-QueueLens-Environment` / `X-QueueLens-Vhost` (the console sends its tab's
choice). `EnvironmentManager` keeps one bundle of services per (environment, vhost),
started on first use. The default bundle is also exposed on `app.state`, which is what
alert rules and tests use. A replay policy runs in the scope it was created in.

### Connection management (`RabbitMQConnection`)

- `aio_pika.connect_robust` with a background retry loop (every 5 s) for the initial connect.
- **Health tracking**: `RobustConnection.is_closed` stays `False` while aio-pika reconnects, so
  liveness is tracked through `close_callbacks` / `reconnect_callbacks` instead. `/ready`
  reports 503 the moment the broker connection drops.
- Channels are opened per operation with `on_return_raises=True`, so a mandatory publish that
  the broker cannot route raises `DeliveryError` instead of silently dropping the message.
  This is a load-bearing safety property; see [SAFETY.md](SAFETY.md).

### Message identity (`domain/fingerprint.py`)

RabbitMQ has no stable, addressable message ID, so QueueLens derives a SHA-256 fingerprint
from `(queue, body hash, headers, message_id, timestamp, exchange, routing_key)`.

- Fingerprints are **stable across requeues**: the `redelivered` flag and quorum queues'
  `x-delivery-count` header are left out.
- Two byte-identical messages share a fingerprint. Every mutating action re-scans and
  **refuses to act unless exactly one message matches**; a detail lookup answers 404.
- Fingerprints correlate a preview with the audit log; they aren't globally stable IDs.

### Browsing and snapshots (`message_browser.py`, `snapshots.py`)

A scan `basic_get`s messages, holds them unacked, and requeues them in order in a `finally`
block, so browsing never consumes a message. One scan reads down to the browse depth (or a
64 MiB budget) and is kept for 5 minutes as a **snapshot**: pages, search, filters, export
and "select all matching" all come from it without touching the broker again. A **quorum
queue** puts returned messages at the back, so it is only ever read **whole** and requeued
in read order; one deeper than the browse depth, or with a delivery limit, is refused (409).

**Queue locks.** A scan holds messages unacked, so two scans of one queue side by side would
each see part of it. `QueueLocks` serializes every scan and action on a queue, keyed by
broker, vhost and queue.

### Mutating actions (`MessageOperator.operate`)

One code path drives replay, park and delete, under the queue's lock:

1. Passively declare the source queue (missing queue → 404, nothing consumed).
2. `basic_get` up to the scan limit, computing fingerprints as messages arrive.
3. Require exactly one match, else requeue everything and refuse.
4. Resolve the target first: park **declares** its durable `{queue}.parking`; replay queue
   targets are **passively verified** to exist.
5. Publish (mandatory, confirms on), and only then ack the original. A copy nacks it back.
6. Requeue every other scanned message. Any failure requeues all unacked messages; if the
   channel already died, the broker requeues them itself.

**Bulk** (`bulk_service.py`, `bulk_runs.py`): a dry run records exactly which fingerprints
it found and returns a one-shot token, stored in the database; executing takes the token
with a single `DELETE … RETURNING` and acts per message. `bulk_runs.execute_audited` writes
the attempt, every message's outcome and the batch's, for a person and for a replay policy
alike.

### Audit (`AuditRepository`)

Every broker action writes a `started` event **before** it runs and a `success` / `failed`
event after. If the attempt can't be recorded, the action is refused: audit is a
precondition, not a best effort. Each row carries the acting user, role, environment, vhost
and client address. Account changes, settings keys and alert-rule and replay-policy changes
are audited too; values that can hold credentials never are.

### Authentication (`auth/basic.py`, `auth/proxy.py`)

- **Basic Auth** for accounts from environment variables and invited accounts. A successful
  check is cached for 60 s, but each request still reads the account row, so a password
  change or deactivation applies at once on every replica.
- **SSO behind an authenticating proxy**: an identity header counts only when the TCP peer
  is in `QUEUELENS_TRUSTED_PROXIES`. `KeepPeer` records the peer before
  `ProxyHeadersMiddleware` applies `X-Forwarded-For`, so a forwarded address can't pass for
  the proxy. Roles come from a local account, then groups, then a default
  ([SSO.md](SSO.md)).
- **Failed-login limiter**: per (IP, user) and per IP, counted in the database so it holds
  across replicas.

### Replicas and coordination (`coordination.py`)

On SQLite there is one replica. On PostgreSQL there can be several:

| Shared state | How |
|---|---|
| Queue locks | transaction-level advisory lock per (broker, vhost, queue), on its own unpooled engine |
| Alert engine and replay policies | one leader, holding a session-level advisory lock; another takes over when it dies |
| Schema creation, seeding | an advisory lock, so replicas can start together on an empty database |
| Runtime environments, audit stream switch | re-read from the database every 5 s |
| Bulk tokens, alert fired-state, login failures | rows in the database |
| Browse snapshots | not shared: sticky sessions keep a client on one replica |

### Background tasks (`main.py` lifespan)

| Task | Runs on | Does |
|---|---|---|
| Alert engine | the leader | evaluates rules every `QUEUELENS_ALERT_INTERVAL_SECONDS`, delivers, records fired state |
| Replay policies | the leader | every 30 s, runs each due policy ([POLICIES.md](POLICIES.md)) |
| Retention | every replica | prunes old audit rows and notifications (idempotent) |
| Settings sync | every replica | re-reads runtime environments and the audit stream switch |

### Replay policies (`replay_policies.py`)

`plan()` is pure. It sorts a snapshot's messages into park (died `max_deaths` times), replay
(dead for `backoff × 2^(deaths − 1)`, grouped by the origin queue from x-death), wait, or
skip, oldest first and up to the cap. `PolicyRunner.run` resolves the policy's own
environment and vhost through `EnvironmentManager`, checks each target's consumers, then
acts through bulk dry runs and `execute_audited` as user `policy:<name>`, records the run,
and pauses the policy after three failed runs. A removed environment counts as a failed
run rather than an immediate pause, since another replica's new environment can take up
to 5 s to sync. The death count also reads `x-queuelens-deaths`, because RabbitMQ 4.x
restarts x-death for a republished message (`domain/xdeath.py`).

### DLQ detection (`QueueService`)

A queue is a DLQ when its **name** matches a convention (`.dlq`, `_dlq`, `dead`) or when
**another queue dead-letters into it** via the default exchange. A queue that merely
*declares* `x-dead-letter-*` arguments is a source, not a DLQ.

### Errors and metrics

Domain and infrastructure exceptions are mapped centrally in `main.py`; see
[API.md](API.md#errors). `observability/metrics.py` counts actions, previews, alert
deliveries and policy runs; the broker, DLQ and paused-policy gauges are refreshed at
scrape time.

## Testing strategy

- **Unit and route tests** (`tests/test_*.py`) swap services on `app.state` and use
  in-memory fakes. They're fast and need no broker.
- **Real broker**: `tests/test_integration_rabbitmq.py` runs the journeys against RabbitMQ
  3.13 and 4.1 (auto-skipped when unreachable), with real dead letters for replay policies.
  It exists because the fakes once encoded the same wrong assumptions as the code.
- **Databases and replicas**: `tests/test_databases.py` and `tests/test_replicas.py` run on
  SQLite and, when `QUEUELENS_TEST_POSTGRES_URL` is set, PostgreSQL, with two or three app
  instances standing in for replicas.
- **Acceptance**: `tests/acceptance/run.py` boots QueueLens and checks every feature group
  black-box: 200+ checks, on SQLite and PostgreSQL.
- **Deploy**: `scripts/test_helm_chart.sh` (lint, kubeconform, kind with one replica on SQLite
  and two on PostgreSQL) and `scripts/test_alerting.py` (promtool, amtool, real delivery).
- **Mutation checks**: new safety logic is checked by breaking it on purpose and watching a
  test fail.
