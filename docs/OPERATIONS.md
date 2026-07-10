# Operations

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
works. Kubernetes manifests are intentionally not shipped in Phase 1.

### Broker permissions

Least privilege for the QueueLens AMQP user:

- **read** on inspected queues (browsing, scanning)
- **write** on replay/park targets (publishing) — park needs **configure** to declare
  `{queue}.parking`
- Management API user needs the `monitoring` tag (queue listing only)

## Security posture

**Run QueueLens inside a trusted private network.** Phase 1 has no payload masking — DLQ
messages may contain tokens, emails, and customer data, and the UI shows them in full.

- Change `QUEUELENS_ADMIN_PASSWORD` before anyone else can reach the instance.
- Basic Auth sends credentials per request — terminate **TLS** in front (reverse proxy or
  ingress); the app itself serves plain HTTP.
- One shared admin account is the Phase-1 model; the audit log records the username, so a
  shared account also means a shared audit identity. RBAC is on the roadmap.
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

## Audit store

- SQLite at `QUEUELENS_DATABASE_URL` (compose: named volume `queuelens-data`).
- Schema is created automatically at startup; there are no migrations in Phase 1 — new
  columns require recreating the database or manual DDL.
- **Backup** = copy the `.db` file (stop the app or use `sqlite3 .backup` for a hot copy).
- There is no retention/pruning job yet; the table grows with every action attempt/outcome.
  Prune manually if needed: `DELETE FROM audit_events WHERE timestamp < :cutoff;`
- Teams needing concurrent writers, retention policies, or central storage should wait for
  the PostgreSQL backend (roadmap) — the store is already behind SQLAlchemy asyncio, so the
  swap is a URL change plus migrations.

## Operational behaviors worth knowing

- **Previewed messages show `redelivered=true`** on the broker afterwards — browsing
  requeues, it never consumes. Expect this in the RabbitMQ UI.
- **Actions scan up to `QUEUELENS_REFETCH_WINDOW_SIZE` messages** from the head of the queue
  and briefly hold them unacked. On very deep queues, messages beyond the window cannot be
  acted on until the queue drains or the window is raised.
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
