# QueueLens — Phase 1 Async-Only Scope

## Objective

Deliver the smallest useful QueueLens release:

> An authenticated engineer can inspect RabbitMQ DLQ messages without removing them during browsing, then safely perform one copy replay, move replay, park, or delete action with confirmation and audit logging.

Phase 1 is limited to one RabbitMQ cluster and one deployable Docker Compose setup.

## Phase 1 Features

- Basic authentication.
- Async RabbitMQ connection and reconnect handling.
- Async RabbitMQ Management API client.
- Queue listing and DLQ detection.
- Configured replay targets from environment variables or YAML; no settings UI.
- Safe DLQ message browsing and local-batch search.
- Message detail view with payload, headers, properties, routing data, and timestamps.
- JSON pretty-printing and `x-death` parsing.
- Best-effort message fingerprint generation for display, current-batch selection, bounded re-fetch matching, and audit correlation.
- Single-message copy replay.
- Single-message move replay: publish first, remove only after publish succeeds.
- Single-message parking.
- Single-message deletion with explicit confirmation.
- SQLite audit log for replay, park, delete, authentication, and configuration events.
- Login, dashboard, queue detail, message detail, replay confirmation, and audit screens.
- Dockerfile, Docker Compose, configuration example, and README.

Phase 1 supports single-message replay, move, park, and delete only. Bulk operations are explicitly deferred.

## RabbitMQ Message Identity Limitation

RabbitMQ messages do not have a globally stable queue-position ID that can be randomly addressed later. QueueLens fingerprints are best-effort identifiers calculated from the message body, properties, headers, routing data, and queue name.

Fingerprints are used for display, selection within the current fetched batch, bounded re-fetch matching, and audit correlation; they are not globally stable RabbitMQ message IDs.

For message detail and mutating actions, QueueLens re-fetches a bounded window and acts only when exactly one message matches the selected fingerprint. If zero or multiple messages match, the request fails safely and the user must refresh.

## Explicitly Out of Scope

Defer these to later phases:

- Bulk operations.
- Read-only mode.
- Sensitive-field masking.
- Slack, email, and generic alerts.
- Prometheus metrics.
- PostgreSQL.
- Kubernetes and Helm deployment.
- Advanced RBAC, SSO/OIDC, multi-user database accounts, and per-queue permissions.
- Message indexing, historical storage, schema validation, tracing, AI analysis, and multi-cluster support.
- Settings/configuration UI.

Read-only mode is a candidate for Phase 1.5 before alerts, metrics, or PostgreSQL.

## Async-Only Engineering Rule

Every external-I/O path in Phase 1 must be asynchronous from the HTTP handler to the dependency boundary and back.

### Required stack

- Python 3.12+
- FastAPI async route handlers
- `aio-pika` for AMQP
- `httpx.AsyncClient` for the RabbitMQ Management API
- SQLAlchemy asyncio with `aiosqlite` for audit storage
- Async application lifespan and shutdown handling

### Prohibited in application code

- `pika`, synchronous HTTP clients, synchronous SQLAlchemy sessions, or blocking filesystem/network calls.
- Blocking RabbitMQ calls inside FastAPI handlers.
- Creating a new client or connection for every request.
- `time.sleep`; use `asyncio.sleep` for retry/backoff.
- Unbounded concurrent operations.

Tests and code review must verify that Phase 1 request paths do not use known blocking clients such as `pika`, `requests`, synchronous SQLAlchemy sessions, or `time.sleep`.

Small in-memory transformations such as JSON formatting, header parsing, and hashing may remain ordinary functions; they must not perform I/O.

## Async Runtime Design

### Application lifecycle

1. Async startup loads and validates configuration.
2. Async RabbitMQ clients are created once and owned by the application lifespan.
3. A reconnect loop uses bounded exponential backoff.
4. Async shutdown cancels retry tasks and closes all clients, channels, and database sessions.

### Request handling

- All API routes are declared with `async def`.
- Dependencies yield async database sessions and shared async RabbitMQ clients.
- Operation and connection timeouts use `asyncio.timeout` or client-native async timeouts.
- RabbitMQ failures become structured application errors; they must not crash the process.

### Browsing safety

- Message preview/search uses async AMQP `basic_get` and explicitly requeues every fetched message before returning.
- Browsing never acknowledges or removes a message.
- Preview operations have a configurable message-count and payload-size limit.

### Mutating actions

Single-message Phase 1 actions may await completion and return their final result, but the implementation remains fully async:

1. Persist an audit attempt; reject the action if this cannot be persisted.
2. Re-fetch a bounded batch from the source DLQ.
3. Match the selected fingerprint and continue only if exactly one match exists.
4. For copy replay, publish asynchronously and keep the original message.
5. For move or park, publish asynchronously first, then remove the matched source message only after publish succeeds.
6. For delete, remove only after explicit confirmation and a successful audit attempt.
7. Persist the final audit result as succeeded or failed.

If publish or audit persistence fails, the original message must remain in the DLQ whenever technically possible; destructive actions fail closed by default.

## Phase 1 API

```text
GET  /health
GET  /ready

GET  /api/queues
GET  /api/queues/{queue_name}
GET  /api/queues/{queue_name}/messages
GET  /api/queues/{queue_name}/messages/{fingerprint}

POST /api/messages/replay
POST /api/messages/park
POST /api/messages/delete

GET  /api/audit
GET  /api/dashboard
```

All endpoints are async. Destructive endpoints require authentication, explicit confirmation, and an audit event before the action is committed.

The message-detail endpoint is a best-effort lookup limited to the current preview batch or bounded re-fetch window. If the message is not found uniquely, the API returns a safe not-found response.

## Phase 1 Data Model

Only the minimum persistent model is required:

### `audit_events`

- `id`
- `timestamp`
- `username`
- `action`
- `source_queue`
- `message_fingerprint`
- `payload_hash`
- `target_type`
- `target_exchange`
- `target_queue`
- `target_routing_key`
- `result`
- `error_message`
- `request_ip`
- `user_agent`
- `metadata_json`

Users and replay targets remain configuration-backed in Phase 1.

SQLite is the default Phase 1 audit store for simple self-hosted deployments. PostgreSQL is deferred for teams that need stronger concurrency, retention, and operational guarantees.

## Phase 1 Operation Limits

```env
QUEUELENS_MAX_PREVIEW_MESSAGES=100
QUEUELENS_MAX_MESSAGE_SIZE_BYTES=1048576
QUEUELENS_OPERATION_TIMEOUT_SECONDS=10
QUEUELENS_REFETCH_WINDOW_SIZE=100
```

These limits protect the app from large queues, oversized messages, and expensive accidental operations.

## Sensitive-Data Warning

Phase 1 does not include sensitive-field masking. QueueLens should not be exposed publicly; run it only inside a trusted private network or behind secure internal access controls because DLQ payloads may contain tokens, emails, customer data, or internal identifiers.

## Phase 1 Acceptance Criteria

1. Docker Compose starts QueueLens and its local RabbitMQ dependency.
2. An authenticated user can see queues and DLQ candidates.
3. Queue and message reads use async clients only.
4. Opening or searching a queue never removes messages.
5. A user can inspect payload, headers, properties, and parsed `x-death` data.
6. A user can copy-replay one message without removing the source message.
7. A user can move one message only after asynchronous publish succeeds.
8. A user can park or delete one message after explicit confirmation.
9. Failed publish never causes the original message to be removed.
10. Replay, park, delete, login, and failures are written to the async SQLite audit log.
11. Temporary RabbitMQ or database failures return clear errors and do not crash the app.
12. Tests and code review verify that Phase 1 request paths do not use known blocking clients such as `pika`, `requests`, synchronous SQLAlchemy sessions, or `time.sleep`.
13. A mutating action fails safely when zero or multiple messages match the selected fingerprint in the bounded re-fetch window.
14. Delete acts only when exactly one message is uniquely matched.

## Internal Phase 1 Milestones

### Phase 1A — Inspect Only

- Async app lifecycle and configuration.
- RabbitMQ Management API client and queue listing.
- DLQ detection.
- Safe message browsing and detail view.
- JSON pretty-printing and `x-death` parsing.
- Docker Compose foundation.

### Phase 1B — Safe Actions

- Copy replay.
- Move replay.
- Parking and explicit confirmation.
- Delete with unique bounded matching.
- Async audit attempt/final-result flow.
- Fail-closed safety rules.

### Phase 1C — Polish

- UI screens and action controls.
- Unit and integration tests.
- README, screenshots, and safety documentation.

## Build Order

1. Async configuration, lifespan, health/readiness, and Docker Compose.
2. Async RabbitMQ Management API and AMQP adapters with reconnect behavior.
3. Authentication and async audit repository.
4. Queue discovery and safe message browsing.
5. Message detail, fingerprinting, JSON rendering, and `x-death` parsing.
6. Copy replay, move replay, park, and delete with fail-closed safety rules.
7. Phase 1 screens, integration tests, README, and safety documentation.

## Definition of Done

Phase 1 is complete only when the core flow works end to end:

```text
login
  -> list queues
  -> open DLQ
  -> inspect message safely
  -> confirm action
  -> async publish/remove operation
  -> async audit write
  -> show result
```

No Phase 2 feature should be added before this flow is tested against a real RabbitMQ instance.
