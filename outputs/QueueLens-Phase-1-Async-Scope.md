# QueueLens — Phase 1 Async-Only Scope

## Objective

Deliver the smallest useful QueueLens release:

> An authenticated engineer can inspect a RabbitMQ DLQ message and safely replay, move, park, or delete it without losing it during browsing.

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
- Stable message fingerprint generation.
- Copy replay.
- Move replay: publish first, remove only after publish succeeds.
- Parking a message.
- Deleting a message with explicit confirmation.
- SQLite audit log for replay, park, delete, authentication, and configuration events.
- Login, dashboard, queue detail, message detail, replay confirmation, and audit screens.
- Dockerfile, Docker Compose, configuration example, and README.

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

- Message preview/search uses the Management API `get` endpoint with `ackmode=ack_requeue_true`.
- Browsing never acknowledges or removes a message.
- Preview operations have a configurable message-count and payload-size limit.

### Mutating actions

Single-message Phase 1 actions may await completion and return their final result, but the implementation remains fully async:

1. Fetch the target message safely.
2. For replay/park, publish asynchronously.
3. Confirm publish success.
4. For move/park/delete, acknowledge/remove only after the required safety condition succeeds.
5. Write the audit event through the async database session.
6. Return a structured success or failure result.

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
12. Tests verify no blocking client/library is used in Phase 1 request paths.

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
