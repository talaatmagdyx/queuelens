# Configuration

All settings come from environment variables with the `QUEUELENS_` prefix
(pydantic-settings, case-insensitive). Defaults target the bundled Docker Compose setup.
Copy [`.env.example`](../.env.example) to `.env` for a documented starting point.

## Sources & precedence — who wins on conflict

QueueLens reads configuration from three places. When the same knob appears in more
than one, this is the order (highest wins):

1. **Real environment variables** — compose `environment:`, Kubernetes env, shell exports.
2. **`.env` file** in the working directory (loaded by pydantic-settings; gitignored).
3. **Built-in defaults** (the tables below).

Separately, the **settings store** (a table in `QUEUELENS_DATABASE_URL`) holds everything
managed at runtime from the UI. It is not a fourth layer of the same knobs — it owns
*different* knobs — but a few areas touch both worlds. The exact rules:

| Area | Env vars / `.env` provide | UI / settings store provides | On conflict |
|---|---|---|---|
| **Environments** | Full profiles via `QUEUELENS_ENVIRONMENTS_JSON` (own broker + credentials) | Runtime-added profiles and extra vhosts (`POST /api/environments`) | Merged by name at startup: a stored entry with the same name **adds vhosts and overrides broker fields** on top of the env-var profile. The default environment itself always comes from env vars |
| **Preview / bulk limits** | `QUEUELENS_MAX_PREVIEW_MESSAGES`, `QUEUELENS_MAX_BULK_SIZE`, … as defaults | Configuration → Limits saves overrides | **Stored overrides win** at request time; "Reset to Defaults" returns to the env-var values |
| **Email channel** | `QUEUELENS_SMTP_HOST/_PORT` **seed** the channel on first boot only | Alerts → Delivery Channels edits (incl. SMTP auth + TLS) | After first boot the **stored channel config wins**; the env vars are never re-applied unless the channel is missing entirely |
| **Users** | `QUEUELENS_ADMIN_*` + `QUEUELENS_USERS_JSON` are seeded into the users table at startup (idempotent — existing rows are not overwritten) and always authenticate | UI invites add more accounts | No conflict possible: env accounts always work; DB accounts add to them |
| **Custom headers, retention, alert rules, UI toggles** | — (no env vars) | Settings store only | n/a |

Practical consequences:

- Changing `QUEUELENS_SMTP_HOST` after first boot does nothing visible — edit the
  channel in Alerts → Delivery Channels instead (or delete the `channels` row).
- Changing an env-var environment's credentials requires a restart; runtime-added
  environments update immediately via the API.
- Wiping the database (`data/queuelens.db`) resets every UI-managed setting to the
  env-var/seeded state on next boot — audit history included, so treat it as data.
- Secrets stored via the UI (SMTP password, environment credentials) are write-only:
  no API response ever includes them.

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
| `QUEUELENS_MAX_BULK_SIZE` | `500` | Scan window and hard cap for bulk operations (dry-run and execute) |
| `QUEUELENS_BULK_DRY_RUN_TTL_SECONDS` | `600` | How long a bulk dry-run token stays executable |

## Masking

| Variable | Default | Meaning |
|---|---|---|
| `QUEUELENS_MASKING_ENABLED` | `true` | Display-only masking of sensitive values in payloads, headers, and properties |
| `QUEUELENS_MASKED_FIELDS` | `password,token,access_token,refresh_token,authorization,api_key,secret,email,phone` | Comma-separated key names whose values render as `***` |

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
| `QUEUELENS_ENVIRONMENT` | `development` | Free-form environment label (name of the default environment) |

## Environments, alerting, and email

| Variable | Default | Purpose |
| --- | --- | --- |
| `QUEUELENS_ENVIRONMENTS_JSON` | `{}` | Additional environment profiles: `{"name": {"rabbitmq_url", "management_url", "management_username", "management_password", "vhosts": [...]}}`. Omitted fields inherit from the default environment, so a same-broker profile only needs `vhosts`. Same-broker environments and extra vhosts can also be added at runtime from Configuration → Environments (persisted in the settings store) |
| `QUEUELENS_SMTP_HOST` | *(empty)* | Seeds the email delivery channel on first boot (e.g. `mailpit`). Channels are editable afterwards in Alerts → Delivery Channels |
| `QUEUELENS_SMTP_PORT` | `1025` | SMTP port for the seeded email channel |
| `QUEUELENS_ALERT_INTERVAL_SECONDS` | `15` | How often the alert engine evaluates enabled rules against live queue stats |

Settings managed in the UI (custom headers, limits overrides, retention, delivery channels,
alert rules, invited users, runtime-added environments) live in the SQLite database
(`QUEUELENS_DATABASE_URL`), not in environment variables.

## Complete example

A production-shaped `.env` ([`.env.example`](../.env.example) is the copy-ready version —
in the real file `QUEUELENS_ENVIRONMENTS_JSON` must stay on a single line):

```bash
# Identity & auth
QUEUELENS_ENVIRONMENT=development
QUEUELENS_ADMIN_USERNAME=admin
QUEUELENS_ADMIN_PASSWORD=use-a-strong-password
QUEUELENS_USERS_JSON={"sre-oncall": "another-strong-password"}

# Default broker (= the "development" environment)
QUEUELENS_RABBITMQ_URL=amqp://queuelens:queuelens@rabbitmq:5672/
QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://rabbitmq:15672
QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME=queuelens
QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=queuelens
QUEUELENS_RABBITMQ_VHOST=/

# More environments — each with its own broker and credentials.
# AMQP credentials live in rabbitmq_url; management credentials may differ
# (e.g. a limited app user for AMQP, a monitoring user for the Management API).
QUEUELENS_ENVIRONMENTS_JSON={
  "staging": {
    "rabbitmq_url": "amqp://stg-user:stg-pass@rabbitmq-stg:5672/",
    "management_url": "http://rabbitmq-stg:15672",
    "management_username": "stg-user",
    "management_password": "stg-pass",
    "vhosts": ["/", "staging"]
  },
  "production": {
    "rabbitmq_url": "amqp://prod-app:prod-app-pass@rabbitmq-prod:5672/",
    "management_url": "https://rabbitmq-prod:15672",
    "management_username": "prod-monitor",
    "management_password": "prod-monitor-pass",
    "vhosts": ["orders", "payments", "billing"]
  }
}

# Email seed (first boot only — edit afterwards in Alerts → Delivery Channels;
# delete these two lines to run without email entirely)
QUEUELENS_SMTP_HOST=mailpit
QUEUELENS_SMTP_PORT=1025
```

What this gives you at runtime:

- Three environments in the top-bar switcher and Configuration panel:
  `development` (default broker), `staging` (own broker, one user for both APIs),
  and `production` (own broker, split AMQP vs management users, three vhosts).
- Activating `production` shows the red type-to-confirm banner; the `orders`,
  `payments`, and `billing` vhosts are created on the broker on first activation.
- Same-broker environments or extra vhosts can still be added later from the UI
  without touching this file.
