# API Reference

Base URL: `http://<host>:8000`. All endpoints except `/health`, `/ready`, and `/login`
require **HTTP Basic Auth** (`QUEUELENS_ADMIN_USERNAME` / `QUEUELENS_ADMIN_PASSWORD`) unless
`QUEUELENS_AUTH_ENABLED=false`.

Interactive OpenAPI docs are served at `/docs` (Swagger UI) and `/redoc`, and the schema at
`/openapi.json` — behind the same Basic Auth as the API.

## Environment and vhost (per request)

Broker endpoints — queues, messages, actions, bulk, `/api/broker`, `/api/exchanges`,
`/api/config`, `/api/topology`, `/api/broker/test`, `/api/metrics/summary` — act on the
environment and vhost the request names:

| Header | Default | Meaning |
|---|---|---|
| `X-QueueLens-Environment` | the default environment (`QUEUELENS_ENVIRONMENT`) | a name from `GET /api/environments` |
| `X-QueueLens-Vhost` | that environment's first vhost (the default's: `QUEUELENS_RABBITMQ_VHOST`) | one of its listed vhosts |

Nothing is instance-global: two clients (or two console tabs) can work against different
brokers at once. An unknown environment or unlisted vhost is `404`, never the default.
Every audit row written for a scoped request carries `metadata.environment` and
`metadata.vhost`. A bulk dry run executes only in the scope it scanned.

`POST /api/environments/activate` (`{"environment", "vhost"?}`) checks that a scope is
reachable (`404` unknown, `502` unreachable) and returns `{"environment", "vhost"}` to
send from then on; it changes nothing for anyone else. `GET /api/environments` marks the
asking request's scope `active` and the default one `default`. `/metrics` and the alert
engine always use the default environment.

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
List queues in the request's vhost (see *Environment and vhost*).

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
      "is_dlq": true,
      "delivery_limit": null,
      "browsable": true
    }
  ]
}
```

`delivery_limit` is a quorum queue's effective delivery limit (`null` when it has none) and
`browsable` is `false` when it has one — exactly the rule that makes previews and actions on
that queue answer `409` (see [SAFETY.md](SAFETY.md#1-browsing-never-consumes-messages)), so
a client can say so before anyone clicks.

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
| `limit` | int 1–1000 | the preview cap | Messages to return (one page) — can lower the cap, never raise it |
| `snapshot` | `new` or an id | — | `new` scans the queue once, down to the browse depth, and keeps that copy for 5 minutes. An id pages through a copy that already exists |
| `offset` | int ≥ 0 | 0 | First message of the page, within the snapshot (after filters) |
| `contains` | string | — | Case-insensitive substring of the raw body, message id, fingerprint, or headers. A compressed body is searched as stored |
| `payload_format` | `json` / `text` / `base64` | — | Only messages of that format |
| `min_deaths` | int ≥ 1 | — | Only messages that died at least this many times (`deaths`, below) |

The cap is the stored *Limits* override if set, else `QUEUELENS_MAX_PREVIEW_MESSAGES`.

**Without `snapshot`, offset or filters,** the response is the head of the queue, as before.
**With them,** QueueLens works from a snapshot:
- One scan reads the queue down to `QUEUELENS_MAX_BROWSE_DEPTH` (default 5000; the Limits
  override goes up to 50 000), or until it has read 64 MiB of bodies. The scan requeues
  everything it read.
- Pages, search and filters are then served from that copy, with **no further broker
  reads and no further deliveries**.
- The response adds `total` (after filters), `offset`, `limit`, and
  `snapshot: {id, scanned, ready, complete, stopped ("depth" / "memory"), depth,
  created_at, expires_at}`.
- An expired or unknown id is a `404`; scan again with `snapshot=new`.
- A snapshot is only ever served back for the environment, vhost and queue it was taken of.

**Quorum queues are always read whole,** for previews, snapshots, lookups and actions alike.
A quorum queue puts every returned message at the back, so reading only part of one would
reorder it. A quorum queue holding more than the browse depth, or more than 64 MiB, is
refused with `409` and an explanation.

`409` for a **quorum queue with a delivery limit** (`x-delivery-limit`, a `delivery-limit`
policy, or the RabbitMQ 4.x default of 20): every requeue counts as a delivery there, so
browsing would eventually drop messages. The same refusal applies to detail lookups, single
actions, and bulk dry runs / executions on that queue. See [SAFETY.md](SAFETY.md#1-browsing-never-consumes-messages).

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
      ],
      "deaths": 1
    }
  ]
}
```

`deaths` is how many times the message has died. It's the sum of its x-death counts,
unless a QueueLens replay stamped a higher number in `x-queuelens-deaths`. RabbitMQ 4.x
restarts x-death for a message that a client republishes, so without that header a message
QueueLens replayed would look like a first death each time it came back.

- `payload_format` is `json`, `text`, or `base64` (auto-detected).
- Payloads larger than `QUEUELENS_MAX_MESSAGE_SIZE_BYTES` are replaced with a truncation
  marker and `payload_truncated: true`.
- Datetimes and raw bytes inside headers/properties/x-death are normalized to strings.
- Values under configured sensitive keys (`QUEUELENS_MASKED_FIELDS`, matching ignores case
  and `-`/`_`) render as `***` in payloads, headers, and properties. Display-only — replay
  always uses the original message. Disable with `QUEUELENS_MASKING_ENABLED=false`.
- Compressed (`gzip`/`deflate`) bodies are shown inflated; `payload_encoded` carries the
  original bytes as base64 — omitted (`null`) when masking hid a value or the payload was
  truncated, since the raw bytes would reveal both.

### `GET /api/queues/{queue_name}/messages/{fingerprint}`
Detail lookup by the full fingerprint, within a bounded re-fetch window (the stored *Limits*
override, else `QUEUELENS_REFETCH_WINDOW_SIZE`). With `?snapshot=<id>`, the message comes
from that snapshot and the broker isn't read. `404` when the fingerprint matches zero **or
multiple** messages; ambiguity is treated as not-found rather than guessing.

### `GET /api/queues/{queue_name}/snapshots/{snapshot_id}/export`
Download a snapshot's messages as `?format=json` (default) or `csv`, with the same
`contains` / `payload_format` / `min_deaths` filters as its pages. Messages are rendered and
masked as the console shows them, and the broker isn't read. CSV columns: `fingerprint`,
`message_id`, `correlation_id`, `timestamp`, `exchange`, `routing_key`, `deaths`,
`payload_format`, `payload`, `headers`; a cell that would open as a spreadsheet formula
is prefixed with `'`. Every export is audited (`export_snapshot`, with the message
count). `404` for an expired snapshot, or one taken in another environment or vhost.

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
`x-queuelens-replayed`, `x-queuelens-action` (`replay_copy`/`replay_move`),
`x-queuelens-replayed-at`, `x-queuelens-replayed-by`, `x-queuelens-source-queue`,
`x-queuelens-original-fingerprint`, `x-queuelens-deaths` (how many times it had died, see
`deaths` above) — plus the admin-configured custom headers. Bulk replay stamps the same
set. A message that had no `message_id` gains a random one on replay (the
client library needs it to match a broker return to its publish).

### `POST /api/messages/park`
Body: `source_queue`, `fingerprint`, `confirm`. Publishes to `{source_queue}.parking`
(durable, declared on demand), then acks the original. Parked messages carry
`x-queuelens-action: park`, `x-queuelens-parked-at`, `x-queuelens-parked-by`,
`x-queuelens-source-queue`, `x-queuelens-original-fingerprint` (bulk park too).

### `POST /api/messages/delete`
Body: `source_queue`, `fingerprint`, `confirm`. Acks (removes) the message.

### `POST /api/messages/publish`
Test message composer. Body: `routing_key` (a queue when `exchange` is `""`), `payload`,
optional `exchange`, `headers` (object, ≤ 64 entries), `properties` (`message_id`,
`correlation_id`, `content_type`, `type`, `app_id`, `reply_to`, `priority`,
`delivery_mode`), `mark_test` (default `true` → `x-queuelens-test`), `confirm`. Mandatory
publish: unroutable → `400`, missing queue/exchange → `404`. The `started` audit event is
written before publishing; `x-queuelens-published-by/-at` always win over caller headers.

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
after the dry run are never touched. The scope is the scan window: the stored *Limits*
override, else `QUEUELENS_MAX_BULK_SIZE`, at most 1000 messages from the head of the queue,
not the whole queue. The batch remembers its window, and execution scans the same.

**Messages picked from a snapshot** (`fingerprints` plus `snapshot`) may lie deeper than the
window. The scan then reaches the deepest selected message, up to the browse depth. A
selection is capped at the bulk limit (`400` above it).

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
| `snapshot` | no | The snapshot the selection was picked from: the scan reaches down to its deepest message |
| `match` | no | Instead of `fingerprints`: every message of `snapshot` matching `{"contains", "payload_format", "min_deaths"}`, the filters of its pages ("select all matching"). Needs `snapshot`; more matches than the bulk limit is `400` |

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

Single actions (`replay`, `park`, `delete`) take the same optional `snapshot` field. With it,
the action scans down to where the snapshot saw the message (plus the re-fetch window as
slack), so anything a snapshot showed can be acted on. Without it, the action scans the
re-fetch window.

`duplicate_fingerprints` counts fingerprints with more than one physical message — those are
**skipped and reported** at execution, never guessed at. `selected_not_seen` counts
explicitly selected fingerprints that are no longer in the scan window (they are ignored). Tokens expire after
`QUEUELENS_BULK_DRY_RUN_TTL_SECONDS`; they are stored in the database (`bulk_batches`), so
they survive a restart until then.

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
    "not_found": 0,
    "not_attempted": 0
  },
  "results": [
    {"fingerprint": "…", "status": "success"},
    {"fingerprint": "…", "status": "skipped_duplicate"}
  ]
}
```

Per-message statuses: `success`, `failed` (with `error`; the message was requeued),
`skipped_duplicate`, `not_found` (no longer in the queue), and `not_attempted`. The last
means the broker closed the channel earlier in the batch (a refused publish or a lost
connection), so the batch stopped and the message stayed in the queue. Each message follows the same
publish-before-ack spine as single actions and fails independently. Audit gets a
`bulk_<action>` `started` event **before** the broker is touched (no audit, no execution),
one event per fingerprint, and a closing envelope whose result is `success` or `partial`.

Errors: `400` missing confirmation / no replay target, `404` unknown or expired batch token,
unknown queue or target, `409` RabbitMQ refused the target before anything moved (e.g. an
existing parking queue declared with other arguments; the detail carries RabbitMQ's
reason), `502` broker failure before anything moved. A refusal or lost connection *during*
the batch is not an error response. The batch stops, and the results report what moved.

## Users

Accounts come from two places. Accounts set by environment variables
(`QUEUELENS_ADMIN_USERNAME`, `QUEUELENS_USERS_JSON`) are changed there. Local accounts are
invited and managed here. A change applies on the account's next request, on every
replica.

### `GET /api/me`
The signed-in user: `username`, `role`, `must_change_password`.

### `GET /api/users`
Every account: `username`, `role`, `email`, `invited_by`, `active`,
`must_change_password`, and `managed` (`env` or `local`).

### `POST /api/users/invite` (Admin)
`{"username", "role": "Admin" | "Operator" | "Viewer", "email"?}`. The response
carries a one-time `password`, shown once; it must be replaced before anything else answers.

### `PATCH /api/users/{username}` (Admin)
`{"role"?, "active"?}`: change a local account's role, or deactivate / reactivate it.
A deactivated account gets `401` with its password and `403` through SSO.
Audited as `update_user`.

### `DELETE /api/users/{username}` (Admin)
Removes a local account; audited as `delete_user`. Someone who signs in through SSO
still gets their group's role afterwards, so deactivate them instead to keep them out.

Both return `400` for your own account or one set by environment variables, and `404`
for an unknown name.

### `POST /api/users/me/password`
`{"current_password", "new_password"}` (10+ characters), for local accounts.

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
`request_ip` / `user_agent` are those of the HTTP request that caused the event (the proxy's
address when QueueLens sits behind one). `metadata.role` is the acting user's role (Admin /
Operator / Viewer) on every row a request writes. Broker actions also carry
`metadata.environment` / `metadata.vhost`. CSV export prefixes cells starting with `= + - @`
with `'` so spreadsheets never evaluate them.

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
