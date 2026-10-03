# Operations

## Deployment model & constraints (read this first)

QueueLens is an **internal-network operations tool**:

- **One replica on SQLite, any number on PostgreSQL.** SQLite is a file on one pod's
  volume. On PostgreSQL the replicas share what has to be shared:
  - **The per-queue lock.** Two replicas never read or act on one queue at once, so a
    quorum queue keeps its order and an action can't miss its message.
  - **The login limiter.** It counts failures from every replica.
  - **One leader for the alert rules.** Only that replica evaluates them, and another
    takes over within one interval if it dies.
  - **Settings.** Runtime environments and the audit-stream switch reach every replica
    within 5 s, and a removed environment stops working everywhere.
  - **Bulk dry-run tokens and alert fired-state,** which already lived in the database.
- **Browse snapshots stay on the replica that took them.** With more than one replica,
  use sticky sessions (the Helm chart's Service pins clients by IP; an ingress
  controller needs its own, see [KUBERNETES.md](KUBERNETES.md#more-than-one-replica)).
  Without them, paging rescans the queue on whichever replica answers.
- **TLS is mandatory and external.** Authentication is HTTP Basic, or SSO through an
  authenticating proxy ([docs/SSO.md](SSO.md)). Always deploy behind a TLS-terminating
  reverse proxy (or a service mesh), and never expose port 8000 directly to the internet.
- **Roles**: Viewer (read-only), Operator (replay/park/publish, alert rules,
  environment switching), Admin (delete, settings, users, environment
  management). Enforced server-side on every route.
- **Environments are chosen per request** (per console tab): switching re-points
  only that tab, so operators can work in different environments and vhosts at
  once. Audit rows record the environment and vhost of every broker action.
- **Alert rules and `/metrics` evaluate the default environment only.**
- Failed logins are rate-limited (10/minute per client IP, in-memory).

## Backups & data

Everything mutable lives in one database (`QUEUELENS_DATABASE_URL`): audit history,
settings, alert rules, notifications, invited users, runtime-added environments. By
default that's the SQLite file `data/queuelens.db` on the `queuelens-data` volume; see
[Database](#database) for PostgreSQL.

- Back SQLite up with `sqlite3 data/queuelens.db ".backup backup.db"` or by
  snapshotting the volume, and PostgreSQL with `pg_dump` or your provider's backups.
  Restoring the database restores everything.
- Retention pruning is **permanent** — export the audit log (CSV/JSON from the
  UI) before shortening retention if you need history.
- Set `QUEUELENS_SECRET_KEY` in production so channel and environment
  credentials are encrypted at rest; back the key up separately from the
  database (one is useless without the other).

## Deployment

### Docker Compose (reference setup)

```bash
docker compose up --build -d
```

Ships the app on `:8000` and a RabbitMQ 3.13 management broker on `:5672`/`:15672`, with the
audit database on a named volume (`queuelens-data`). For an existing broker, deploy only the
app image and point `QUEUELENS_RABBITMQ_URL` / `QUEUELENS_RABBITMQ_MANAGEMENT_URL` at it —
see [CONFIGURATION.md](CONFIGURATION.md).

The image is plain `uvicorn app.main:app --host 0.0.0.0 --port 8000`; any container platform
works. On Kubernetes, use the Helm chart in `deploy/helm/queuelens`: [KUBERNETES.md](KUBERNETES.md).

### Broker permissions

Least privilege for the QueueLens AMQP user:

- **read** on inspected queues (browsing, scanning)
- **write** on replay/park targets (publishing) — park needs **configure** to declare
  `{queue}.parking`
- Management API user needs the `monitoring` tag (queue listing only)

## Security posture

**Run QueueLens inside a trusted private network.** Masking hides the configured JSON
fields (`QUEUELENS_MASKED_FIELDS`), but DLQ messages may carry tokens, emails and customer
data elsewhere, in text bodies for instance, and the UI shows those in full.

- Change `QUEUELENS_ADMIN_PASSWORD` before anyone else can reach the instance.
- Basic Auth sends credentials per request — terminate **TLS** in front (reverse proxy or
  ingress); the app itself serves plain HTTP.
- Give people their own identity: invite them (Users page), or put QueueLens behind your
  SSO with an authenticating proxy ([docs/SSO.md](SSO.md)). A shared account means a
  shared audit identity.
- When someone leaves, deactivate or remove their account on the Users page. It takes
  effect on their next request, on every replica. Accounts set by environment variables
  are removed there.
- `X-Forwarded-For` / `-Proto` are believed only from `QUEUELENS_TRUSTED_PROXIES`
  (default loopback). The image runs uvicorn with `--no-proxy-headers`, so uvicorn's
  `FORWARDED_ALLOW_IPS` doesn't apply.
- The app makes no outbound calls except to the configured broker and its Management API.

## Health probes

| Endpoint | Use as | Semantics |
|---|---|---|
| `GET /health` | Liveness | Process is up |
| `GET /ready` | Readiness | Startup complete **and** AMQP connection live; flips to 503 during a broker outage and recovers automatically |

The app keeps serving during a broker outage: pages that need the broker return 503 with a
clear message, and a background loop retries the initial connection every 5 s (aio-pika's
robust connection handles reconnects after that).

## Monitoring

`GET /metrics` serves Prometheus metrics behind the same Basic Auth as the app:

```yaml
scrape_configs:
  - job_name: queuelens
    metrics_path: /metrics
    basic_auth:
      username: admin
      password: change-me
    static_configs:
      - targets: ["queuelens.internal:8000"]
```

The two gauges (`queuelens_rabbitmq_ready`, `queuelens_dlq_messages{queue}`) are refreshed
at scrape time from the live broker, so each scrape costs one Management API call. Counters
(`queuelens_actions_total`, `queuelens_preview_requests_total`) and the operation-duration
histogram accumulate in process — they reset on restart, as Prometheus counters are
expected to.

Ready-made alert rules (broker down, DLQ above threshold, DLQ growing, action failures)
ship in [`deploy/prometheus/alerts.yml`](../deploy/prometheus/alerts.yml); tune the
thresholds to your traffic.

Delivering them to Slack or a webhook through Alertmanager: [ALERTING.md](ALERTING.md).

## Database

- **SQLite** (default, zero configuration): `sqlite+aiosqlite:///./data/queuelens.db`.
- **PostgreSQL**, for central storage, `pg_dump` / point-in-time backups and the database
  tooling you already run: `postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB`, or without
  the password and with `PGPASSWORD` set, which keeps it out of the URL. The driver
  is in the image; CI runs the whole acceptance suite on PostgreSQL 17. With compose, add
  [`docker-compose.postgres.yml`](../docker-compose.postgres.yml).
- Tables are created at startup, and columns added by later releases are added then too.
- Retention (Configuration → Retention) prunes audit rows and notifications older than N
  days, at every start.

### Moving to PostgreSQL

`python -m app.copy_db SOURCE_URL [TARGET_URL]` copies every table in one transaction,
checks each table's row count, and refuses a target that already has rows. The target
defaults to `QUEUELENS_DATABASE_URL`, which keeps the password off the command line. With
compose:

1. Stop QueueLens: `docker compose stop queuelens`.
2. Set `POSTGRES_PASSWORD` in `.env` (see the top of `docker-compose.postgres.yml`).
3. Copy, before QueueLens first starts on PostgreSQL (its first start would seed the
   database, and the copy would then refuse):

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.postgres.yml run --rm queuelens \
     python -m app.copy_db sqlite+aiosqlite:///./data/queuelens.db
   ```

4. Start: `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d`.

Keep the same `QUEUELENS_SECRET_KEY`: encrypted settings are copied as they are. Keep the
SQLite file until you're satisfied.

## Operational behaviors worth knowing

- **Previewed messages show `redelivered=true`** on the broker afterwards — browsing
  requeues, it never consumes. Expect this in the RabbitMQ UI.
- **Actions scan up to `QUEUELENS_REFETCH_WINDOW_SIZE` messages** from the head of the queue
  and briefly hold them unacked.
  - A message picked from a snapshot is reached however deep it is, up to the browse depth
    (`QUEUELENS_MAX_BROWSE_DEPTH`, default 5000).
  - A snapshot scan holds up to that many messages unacked, which takes about 2 s per 5000
    on a local broker.
- **Quorum queues are always scanned whole**, to keep their order. A quorum DLQ deeper than
  the browse depth answers `409` until it drains, the depth is raised, or it is shovelled to
  a classic queue.
- **Park auto-creates `{queue}.parking`** (durable, default exchange). Parked messages stay
  there until you replay or delete them — QueueLens does not consume from parking queues.
- **Replayed messages carry `x-queuelens-*` headers** (who, when, from where, original
  fingerprint). Point consumers at `x-queuelens-original-fingerprint` for idempotency.

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| `/ready` 503, `/health` 200 | Broker unreachable | Broker up? `QUEUELENS_RABBITMQ_URL` correct? App logs show connect errors |
| Dashboard 503 | Management API unreachable | `QUEUELENS_RABBITMQ_MANAGEMENT_URL`, credentials, `monitoring` tag |
| Queue exists but 404 in QueueLens | Wrong vhost | `QUEUELENS_RABBITMQ_VHOST` |
| Action returns 409 | Duplicate or vanished message | Refresh the queue view; byte-identical duplicates cannot be mutated individually |
| Replay returns 400 "No replay target configured" | No target in request or config | Type a target in the UI or set `QUEUELENS_REPLAY_TARGETS_JSON` |
| Expected DLQ missing from dashboard | Name matches no convention and nothing dead-letters into it | Rename per convention, or open it directly via `/queues/{name}` |
