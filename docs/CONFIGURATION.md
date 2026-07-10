# Configuration

All settings come from environment variables with the `QUEUELENS_` prefix
(pydantic-settings, case-insensitive). Defaults target the bundled Docker Compose setup.

## Authentication

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_AUTH_ENABLED` | `true` | HTTP Basic Auth on every page and API route (except `/health`, `/ready`, `/login`). Set `false` only for local single-user use |
| `QUEUELENS_ADMIN_USERNAME` | `admin` | Basic Auth username |
| `QUEUELENS_ADMIN_PASSWORD` | `change-me` | Basic Auth password — **change it** anywhere shared |

## RabbitMQ

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | AMQP connection URL (browsing + actions) |
| `QUEUELENS_RABBITMQ_MANAGEMENT_URL` | `http://rabbitmq:15672` | Management API base URL (queue discovery) |
| `QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME` | `guest` | Management API user — needs at least the `monitoring` tag |
| `QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD` | `guest` | Management API password |
| `QUEUELENS_RABBITMQ_VHOST` | `/` | Vhost to inspect |
| `QUEUELENS_RABBITMQ_CONNECTION_NAME` | `queuelens` | Connection name shown in the RabbitMQ UI |
| `QUEUELENS_RABBITMQ_OPERATION_TIMEOUT_SECONDS` | `10.0` | Connect + Management API timeout |

The AMQP user needs read/write/configure on the inspected queues: browsing requeues
(`basic.get`/`basic.nack`), actions publish and ack, and park **declares** its parking queue.

## Storage

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_DATABASE_URL` | `sqlite+aiosqlite:///./data/queuelens.db` | Audit store (SQLAlchemy asyncio URL). Schema is auto-created at startup |

## Limits

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_MAX_PREVIEW_MESSAGES` | `100` | Messages fetched per queue preview |
| `QUEUELENS_MAX_MESSAGE_SIZE_BYTES` | `1048576` | Payloads larger than this are truncated in responses (the message itself is untouched) |
| `QUEUELENS_REFETCH_WINDOW_SIZE` | `100` | How many messages detail lookup and actions re-scan to find a fingerprint. Raise it for deep queues — actions can only act on messages within this window |

## Masking

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_MASKING_ENABLED` | `true` | Display-only masking of sensitive values in payloads, headers, and properties |
| `QUEUELENS_MASKED_FIELDS` | `password,token,access_token,refresh_token,authorization,api_key,secret,email,phone` | Comma-separated key names whose values render as `•••` |

Key matching ignores case and `-`/`_` separators, so `api_key` also masks `API-Key` and
`apiKey`. Masking is **display-only**: it applies where messages are rendered (UI and read
API) and never modifies the stored message or a replay payload. It is key-based — values
containing secrets under unlisted keys are not detected. See
[SAFETY.md](SAFETY.md#known-limits-phase-1-by-design).

## Replay targets

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_REPLAY_TARGETS_JSON` | `{}` | JSON object mapping source queue → default replay target |

```json
{
  "orders.created.dlq": {
    "type": "exchange",
    "exchange": "orders.exchange",
    "routing_key": "orders.created"
  },
  "email.delivery.dlq": {
    "type": "queue",
    "queue": "email.delivery"
  }
}
```

A target supplied in the replay request (or typed into the message-detail UI) always wins
over this mapping. With neither present, replay fails with `400`.

See [`config/replay-targets.example.json`](../config/replay-targets.example.json).

## Misc

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_APP_NAME` | `QueueLens` | Display name |
| `QUEUELENS_ENVIRONMENT` | `development` | Free-form environment label |
