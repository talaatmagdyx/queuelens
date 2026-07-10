# API Reference

Base URL: `http://<host>:8000`. All endpoints except `/health`, `/ready`, and `/login`
require **HTTP Basic Auth** (`QUEUELENS_ADMIN_USERNAME` / `QUEUELENS_ADMIN_PASSWORD`) unless
`QUEUELENS_AUTH_ENABLED=false`.

Interactive OpenAPI docs are served at `/docs` (Swagger UI) and `/redoc`.

## Health

### `GET /health`
Liveness. Always `200 {"status": "ok"}` while the process runs.

### `GET /ready`
Readiness. `200 {"status": "ok"}` only when startup finished **and** the AMQP connection is
live; `503 {"status": "not_ready"}` otherwise (including mid-outage — connection loss is
tracked via close/reconnect callbacks, not just socket state). Use this for load-balancer and
Kubernetes readiness probes.

### `GET /metrics`
Prometheus metrics (requires Basic Auth like the rest of the app — configure
`basic_auth` in your scrape job):

| Metric | Type | Meaning |
|---|---|---|
| `queuelens_rabbitmq_ready` | gauge | 1 when the AMQP connection is live (refreshed at scrape time) |
| `queuelens_dlq_messages{queue}` | gauge | Messages in each detected DLQ (refreshed at scrape time) |
| `queuelens_preview_requests_total` | counter | Queue previews served (UI + API) |
| `queuelens_actions_total{action,result}` | counter | Actions by result; `bulk_<action>` rows are batch envelopes, plain rows count individual messages |
| `queuelens_operation_duration_seconds{action}` | histogram | Broker operation duration |

Example alert rules ship in [`deploy/prometheus/alerts.yml`](../deploy/prometheus/alerts.yml).

## Queues

### `GET /api/queues`
List queues in the configured vhost.

| Query param | Type | Default | Meaning |
|---|---|---|---|
| `dlq_only` | bool | `false` | Only queues detected as DLQs |

```json
{
  "queues": [
    {
      "name": "orders.processing.dlq",
      "vhost": "/",
      "messages": 3,
      "messages_ready": 3,
      "messages_unacked": 0,
      "consumers": 0,
      "durable": true,
      "arguments": {},
      "is_dlq": true
    }
  ]
}
```

`queue_type` is the RabbitMQ queue type (`classic`, `quorum`, or `stream`), read from the
Management API `type` field with an `x-queue-type` argument fallback.

`is_dlq` is true when the name matches `.dlq` / `_dlq` / `dead`, or when another queue
dead-letters into it via the default exchange.

### `GET /api/queues/{queue_name}`
Single queue stats, same shape under `"queue"`. `404` if the queue does not exist.

## Messages

### `GET /api/queues/{queue_name}/messages`
Non-destructive preview. Messages are fetched with `basic_get` and requeued afterwards —
nothing is consumed (the broker's `redelivered` flag will be set).

| Query param | Type | Default | Meaning |
|---|---|---|---|
| `limit` | int 1–100 | `100` | Max messages to preview |

```json
{
  "messages": [
    {
      "fingerprint": "0a5d5c9d…(sha-256 hex)",
      "queue": "orders.processing.dlq",
      "payload": {"order_id": "ord_8231", "status": "payment_failed"},
      "payload_truncated": false,
      "payload_format": "json",
      "payload_size": 98,
      "content_type": "application/json",
      "message_id": "ord_8231",
      "correlation_id": null,
      "timestamp": null,
      "exchange": "",
      "routing_key": "orders.processing.dlq",
      "headers": {"x-death": [ … ]},
      "properties": { … full AMQP properties … },
      "redelivered": true,
      "x_death": [
        {
          "count": 1,
          "exchange": "",
          "queue": "orders.processing",
          "reason": "rejected",
          "routing-keys": ["orders.processing"],
          "time": "2026-07-10T00:26:23+00:00"
        }
      ]
    }
  ]
}
```

- `payload_format` is `json`, `text`, or `base64` (auto-detected).
- Payloads larger than `QUEUELENS_MAX_MESSAGE_SIZE_BYTES` are replaced with a truncation
  marker and `payload_truncated: true`.
- Datetimes and raw bytes inside headers/properties/x-death are normalized to strings.
- Values under configured sensitive keys (`QUEUELENS_MASKED_FIELDS`, matching ignores case
  and `-`/`_`) render as `***` in payloads, headers, and properties. Display-only — replay
  always uses the original message. Disable with `QUEUELENS_MASKING_ENABLED=false`.

### `GET /api/queues/{queue_name}/messages/{fingerprint}`
Detail lookup by fingerprint (min length 8) within a bounded re-fetch window
(`QUEUELENS_REFETCH_WINDOW_SIZE`). `404` when the fingerprint matches zero **or multiple**
messages — ambiguity is treated as not-found rather than guessing.

## Actions

All actions are `POST`, require `"confirm": true`, and follow the same lifecycle: audit
`started` event → execute → audit `success`/`failed` event. See [SAFETY.md](SAFETY.md) for
the delivery guarantees.

### `POST /api/messages/replay`

```json
{
  "source_queue": "orders.processing.dlq",
  "fingerprint": "0a5d5c9d…",
  "mode": "copy",
  "confirm": true,
  "target": {"type": "queue", "queue": "orders.processing.retry"}
}
```

| Field | Required | Meaning |
|---|---|---|
| `source_queue` | yes | Queue containing the message |
| `fingerprint` | yes (min 8 chars) | Message identity from a listing |
| `mode` | no (`copy`) | `copy` keeps the original; `move` removes it after publish succeeds |
| `target` | no | `{"type": "queue", "queue": …}` or `{"type": "exchange", "exchange": …, "routing_key": …}`. Falls back to `QUEUELENS_REPLAY_TARGETS_JSON[source_queue]`; if neither exists → `400` |
| `confirm` | yes | Must be `true` |

Replayed messages keep their body and properties and gain provenance headers:
`x-queuelens-replayed`, `x-queuelens-replayed-at`, `x-queuelens-replayed-by`,
`x-queuelens-source-queue`, `x-queuelens-original-fingerprint`.

### `POST /api/messages/park`
Body: `source_queue`, `fingerprint`, `confirm`. Publishes to `{source_queue}.parking`
(durable, declared on demand), then acks the original.

### `POST /api/messages/delete`
Body: `source_queue`, `fingerprint`, `confirm`. Acks (removes) the message.

**Action success response:**

```json
{
  "status": "success",
  "action": "replay",
  "fingerprint": "0a5d5c9d…",
  "target": {"type": "queue", "queue": "orders.processing.retry", "exchange": null, "routing_key": null}
}
```

## Bulk actions

Bulk operations are **two-phase**: a dry run captures exactly which messages were seen and
returns a one-shot token; execution acts only on that approved set. Messages that arrive
after the dry run are never touched. Scope is the scan window (up to
`QUEUELENS_MAX_BULK_SIZE` messages from the head of the queue), not the whole queue.

### `POST /api/messages/bulk/dry-run`

```json
{
  "source_queue": "orders.processing.dlq",
  "action": "replay",
  "mode": "move",
  "target": {"type": "queue", "queue": "orders.processing"},
  "payload_contains": "\"tenant\": \"acme\""
}
```

| Field | Required | Meaning |
|---|---|---|
| `source_queue` | yes | Queue to act on |
| `action` | yes | `replay`, `park`, or `delete` |
| `mode` | no (`copy`) | Replay mode |
| `target` | no | Replay target; falls back to the configured target, else `400` |
| `payload_contains` | no | Only messages whose raw body contains this substring |
| `fingerprints` | no (max 1000) | Explicit selection: only these fingerprints are considered (combines with `payload_contains` as an intersection) |

Response:

```json
{
  "batch_id": "…one-shot token…",
  "message_count": 5,
  "unique_fingerprints": 4,
  "duplicate_fingerprints": 1,
  "selected_not_seen": 0,
  "sample_fingerprints": ["…first 10…"],
  "expires_at": "2026-07-10T02:40:00+00:00",
  "scan_limit": 500
}
```

`duplicate_fingerprints` counts fingerprints with more than one physical message — those are
**skipped and reported** at execution, never guessed at. `selected_not_seen` counts
explicitly selected fingerprints that are no longer in the scan window (they are ignored). Tokens expire after
`QUEUELENS_BULK_DRY_RUN_TTL_SECONDS` and live in process memory (a restart voids them —
rerun the dry run).

### `POST /api/messages/bulk/execute`

```json
{"batch_id": "…token from dry-run…", "confirm": true}
```

Executes at most one batch at a time (a second call waits). The token is consumed on
execution. Response:

```json
{
  "action": "park",
  "source_queue": "orders.processing.dlq",
  "summary": {
    "fingerprints_requested": 4,
    "succeeded": 3,
    "failed": 0,
    "skipped_duplicates": 1,
    "not_found": 0
  },
  "results": [
    {"fingerprint": "…", "status": "success"},
    {"fingerprint": "…", "status": "skipped_duplicate"}
  ]
}
```

Per-message statuses: `success`, `failed` (with `error`; the message was requeued),
`skipped_duplicate`, `not_found` (no longer in the queue). Each message follows the same
publish-before-ack spine as single actions and fails independently. Audit gets one event per
fingerprint plus a `bulk_<action>` envelope whose result is `success` or `partial`.

Errors: `400` missing confirmation / no replay target, `404` unknown or expired batch token,
unknown queue or target, `502` channel-level broker failure (the whole batch aborts and the
broker requeues everything unacked).

## Audit

### `GET /api/audit`

| Query param | Type | Default | Meaning |
|---|---|---|---|
| `action` | str | – | e.g. `replay`, `park`, `delete` |
| `username` | str | – | Acting user |
| `source_queue` | str | – | Queue filter |
| `result` | str | – | `started`, `success`, `failed` |
| `limit` | int 1–500 | `100` | Newest first |

Event fields: `id`, `timestamp`, `username`, `action`, `source_queue`,
`message_fingerprint`, `payload_hash`, `target_type`, `target_exchange`, `target_queue`,
`target_routing_key`, `result`, `error_message`, `request_ip`, `user_agent`, `metadata`.

## Errors

Errors under `/api/*` are `{"detail": "<message>"}`; the same conditions render a friendly
error page on web routes.

| Status | Condition |
|---|---|
| `400` | Missing confirmation; no replay target available; invalid target shape; **unroutable publish** (target exchange routes nowhere) |
| `401` | Missing/invalid Basic Auth credentials |
| `404` | Unknown queue (source or replay target); fingerprint matched zero or multiple messages on detail lookup |
| `409` | Mutating action where the fingerprint did not match exactly one message — refresh and retry |
| `422` | Request body failed validation (FastAPI/pydantic) |
| `502` | RabbitMQ Management API error; unexpected broker failure during an action |
| `503` | Broker or Management API unreachable; app not ready |

Failed actions always leave the original message in the source queue and write a `failed`
audit event with the error message.

## Web routes (HTML)

| Route | Page |
|---|---|
| `GET /login` | Landing page (Basic Auth prompt happens on first protected page) |
| `GET /` | DLQ dashboard |
| `GET /queues/{queue_name}` | Queue message preview |
| `GET /messages/{queue_name}/{fingerprint}` | Message detail + actions |
| `GET /audit` | Audit log (latest 100) |
