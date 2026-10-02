# Changelog

## v0.11.0 — 2026-10-02

### Changed
- **Environments are chosen per request, not per instance.** Each request names its
  environment and vhost (`X-QueueLens-Environment` / `X-QueueLens-Vhost`; default
  environment without them), and the console keeps the choice per tab. One operator's
  switch no longer re-points everyone else's views and actions, and two vhosts can be
  browsed at once. `POST /api/environments/activate` now only checks a scope is reachable;
  the "Environment switched" notification is gone (nothing changes for anyone else).
- An environment in use can be removed; its connections close and requests naming it get
  `404`. The Metrics screen shows the tab's environment; `/metrics` and alert rules use the
  default one.

### Fixed
- **A bulk run that the broker stopped midway no longer hides what it already did.** A
  refused publish (e.g. another user's `user_id`) or a dropped connection closes the
  channel. The batch used to answer `502 Bulk operation failed`, while the messages before
  that point had already moved, without per-message audit rows. Now the batch stops, the
  response reports every outcome (`failed` with RabbitMQ's reason, `not_attempted` for the
  rest, which stay queued), and each one is audited. The result is `partial`.
- **Replays keep `expiration` to the millisecond.** aio-pika decodes the TTL to float
  seconds and truncates it when re-encoding, so some values changed (`"1001"` became
  `"1000"`, `"65526"` became `"65525"`). This affects aio-pika 9 and 10.
- **A replay the broker refuses explains itself.** `409` carries RabbitMQ's reason instead of
  a bare `502 Message operation failed`, including the hint for `user_id`: RabbitMQ only
  accepts another user's `user_id` from that user or an `impersonator`. Nothing changes.
- Describing a broker error can no longer break a failure path. aiormq 7's
  `DeliveryError.__str__` raises when the error carries no frame. That could abort a whole
  bulk batch with a 502, and it lost the error text from failed actions' audit rows.

### Safety
- Audit rows for broker actions record `metadata.environment` and `metadata.vhost`.
- A bulk dry run executes only in the environment/vhost it scanned. The batch store is
  shared, and a same-named queue elsewhere can hold identical messages.

### Security
- The verified-login cache key is an HMAC-SHA256 under a random per-process key, instead of
  SHA-256(pepper + password). It never stored passwords, but a keyed lookup is the right
  construction.
- CI workflows run with a read-only token by default; only the image publish job can write
  packages.
- CodeQL code scanning (Python, the console's JavaScript, the workflows) runs on every PR.
  Dependabot proposes weekly updates for Python packages, Actions and Docker base images.

### Dependencies
- **aio-pika 10 / aiormq 7 are supported** (`aio-pika>=9.4,<11`). The suite and the
  acceptance run pass on 9.6 and 10.1.
- `cryptography<51`. For development: pytest 9, pytest-asyncio 1.x, mypy 2, and newer
  GitHub Actions.

### Docs
- README and site say where "browsing never consumes" stops: quorum queues with a delivery
  limit, which QueueLens refuses to browse.

### Upgrade notes
- **Scripts that called `POST /api/environments/activate` to point the instance at another
  environment must now send `X-QueueLens-Environment` / `X-QueueLens-Vhost` on their
  requests.** `activate` only checks reachability, and requests without the headers use the
  default environment.
- Bulk dry runs created before the upgrade carry no environment, so they are refused at
  execute. Run the dry run again.
- Additive API fields: `default` on `/api/environments` entries, the `not_attempted`
  per-message status and summary key on bulk execute, and `metadata.environment` /
  `metadata.vhost` on audit rows.

## v0.10.2 — 2026-10-01

### Fixed
- PagerDuty `dedup_key` is per incident (`queuelens-rule-<id>-<fired at>`). Alert-rule ids
  are reused after a delete, so a new rule could merge into — or resolve — a deleted rule's
  still-open incident.

### Security
- Invited accounts must replace their one-time password before anything else: every
  endpoint except `/api/me` and `POST /api/users/me/password` answers `403` until then,
  and the console shows only the password form.

### Added
- The queue list reports each quorum queue's effective `delivery_limit` and whether it is
  `browsable`; the console badges "not browsable" queues (with the fix) on the Dashboard,
  Queues and queue-detail screens — no more discovering the refusal by clicking.
- `tests/acceptance/run.py`: the black-box acceptance run (real broker, real QueueLens
  process, ~200 checks across every feature group), run in CI on RabbitMQ 3.13 and 4.1.

### Performance
- Successful Basic-auth checks for database users are remembered for 60 s (keyed hash, never
  the password; cleared on password change) — PBKDF2 no longer runs on every request
  (~34 ms → ~2 ms per request).

## v0.10.1 — 2026-10-01

### Security / data safety
- **RabbitMQ 3.x: `x-delivery-limit: -1` is no longer treated as unlimited.** On 3.x it drops
  a message on its first return, so 0.10.0 allowed previews that destroyed messages there.
- **RabbitMQ 4.x: a `-1` from the policy (or argument) no longer cancels a real limit from
  the other source** — the lowest non-negative value wins, as the broker does.
- Quorum queues are refused until their first statistics (and applied policy) are visible.
- Invite emails no longer contain the initial password — the inviting admin sees it once
  and hands it over directly.
- `/docs`, `/redoc` and `/openapi.json` require the same authentication as the API.

### Added
- CI runs the integration suite against RabbitMQ 3.13 **and 4.1**, including a real-broker
  check that previews never cost a quorum queue a message.
- Quiet hours follow a configurable time zone (`ui.quiet_tz`, IANA name, validated on save;
  default UTC) — picker in Alerts → Quiet Hours.
- PagerDuty Events API v2 routing key can be set in the UI (write-only, like the URLs), and
  PagerDuty can be selected as a rule channel when creating alert rules.

## v0.10.0 — 2026-10-01

### Security / data safety
- **Quorum DLQs with a delivery limit are no longer browsed.** A preview is basic.get +
  requeue, which quorum queues count as a delivery — previewing could drop messages, and
  the console previewed the largest DLQ on every page load and 30s auto-refresh. Previews,
  detail lookups, single actions and bulk scans now refuse such queues with `409`, and the
  console fetches message bodies only when a screen asks for them.
- Bulk execute and composer publish now write their `started` audit event before touching
  the broker (no audit → no action), like single actions always did.
- Slack/webhook/PagerDuty URLs and the PagerDuty routing key are write-only in
  `GET /api/settings` (they were readable by every role).
- SMTP TLS now verifies the server certificate and hostname.
- Activating an environment only accepts its listed vhosts — an Operator could create
  arbitrary vhosts (and grant permissions) on the broker by switching.
- The raw `payload_encoded` view is withheld when masking hid a value or the payload was
  truncated.
- Failed logins take the same time for unknown and known usernames; throttling is per
  (IP, username) plus an IP-wide ceiling.
- Audit CSV export neutralises spreadsheet formulas.

### Fixed
- Dashboard summary cards show live numbers (they were hard-coded sample values).
- `?limit` on previews can only lower the configured cap; bulk execution scans the same
  window its dry run approved; single actions use the same re-fetch window as detail lookup.
- Topology cache is cleared on environment switch.
- `add_environment` audit events name the acting admin (not the AMQP username).
- PagerDuty: one incident per rule (`dedup_key`), resolved on recovery; recoveries follow the
  rule's severity for quiet hours.
- Bulk replay/park stamp the same provenance (and custom) headers as single actions.
- Audit events record the request's IP and user agent.
- Concurrent previews of one queue no longer see partial results (per-queue scan lock).
- Startup failures report their real cause (no masking `UnboundLocalError`).
- Users screen shows Viewers as Viewers.

### Added
- Composer: per-message headers and properties (`message_id`, `correlation_id`, …).
- `queuelens_alert_deliveries_total{channel,result}` metric.
- `deploy/prometheus/prometheus.yml` example scrape config.
- UI: delete is type-to-confirm; bulk flows show the server's dry-run result before executing;
  icon-only buttons have accessible names.

### Removed
- **The legacy server-rendered console.** Every old page (`/config`, `/queues`,
  `/audit`, `/classic`, …) now 301-redirects to the SPA at `/app`, which has
  been the default and the only UI receiving features for several releases.
  The Jinja templates are gone except for the standalone error page.
- The `outputs/` scoping-notes folder.

## v0.9.0 — 2026-07-11

### Added
- **Topology caching**: the exchanges/bindings/queues snapshot (three
  management-API calls) is now served from a 30-second cache — part of the
  documented "light on your broker" contract.
- README and the landing page now document how QueueLens avoids loading the
  broker, plus a feature-status table (Stable / Experimental / Roadmap).
- **Compressed-payload decode**: messages with `content_encoding: gzip` or
  `deflate` now display their inflated payload (JSON pretty-printed), with a
  decoded/encoded toggle showing the original bytes as base64. Decompression
  is capped at 4 MiB (zip-bomb guard) and is display-only — replay publishes
  the original compressed body unchanged.
- **Metrics screen in the console**: live `queuelens_*` values (broker status,
  DLQ backlog per queue, action counters, average broker-operation latency),
  a ready-to-paste Prometheus scrape config, and the bundled alert rules with
  copy buttons. Backed by `GET /api/metrics/summary` and
  `GET /api/metrics/alert-rules`.

## v0.8.0 — 2026-07-11

### Added
- **Precompiled front end in the container image**: JSX is compiled at image
  build (two-stage Dockerfile) and Babel standalone is removed from the
  browser entirely — faster first paint, CSP-compatible. Local development
  still uses in-browser compilation; the design-system bundle can be built
  anywhere with `python scripts/build_ds_bundle.py`.
- **Durable state**: bulk dry-run tokens now live in the database (they
  survive restarts), and alert fired-state persists on the rule — a restart
  no longer re-sends notifications for conditions that already fired.
- **Full-history audit export**: `GET /api/audit/export?format=csv|json`
  streams the complete audit log; the UI export button now uses it.
- **Browser e2e smoke suite** (Playwright) running in CI against a real
  broker: dashboard, all screens, wizard confirmation gating.
- GHCR images now also carry `vX.Y.Z`-style tags alongside `X.Y.Z`.
- Rewritten README with a fresh screenshot tour of the console.
- App version is now kept in sync between `pyproject.toml` and the API
  (both previously reported 0.5.0).

## v0.7.0 — 2026-07-11

The platform release: everything configurable is now server-backed and real.

### Added
- **Alert engine**: rule CRUD, background evaluation against live queue stats,
  fire + recovery notifications, per-channel delivery with retry (email via
  SMTP incl. auth/TLS, Slack, PagerDuty Events v2, generic webhook), quiet
  hours, test buttons.
- **Multi-environment support**: profiles via `QUEUELENS_ENVIRONMENTS_JSON`
  or created in the UI (own broker host + split AMQP/management credentials),
  vhosts created on first activation, live Set Active, every switch audited
  and broadcast.
- **Server-backed settings**: custom headers (stamped on every publish),
  preview/refetch/bulk limit overrides applied server-side, retention with
  hourly pruning, audit-to-stdout streaming, export format.
- **Users**: DB-backed accounts, Invite User with one-time password (emailed
  when a channel is configured), self-service password change.
- **RBAC**: Viewer (read-only) / Operator (recover) / Admin (delete,
  configuration, users) enforced server-side; `/api/me`.
- **Auth hardening**: failed-login rate limiting (10/min per IP).
- **Secrets at rest**: optional Fernet encryption via `QUEUELENS_SECRET_KEY`;
  all stored secrets are write-only through the API.
- New screens: Parking, Queue Detail, Topology, Composer, Alerts; ⌘K palette.
- `.env` file support + documented `.env.example`; configuration precedence docs.
- Container: non-root user, `HEALTHCHECK`.

### Changed
- SPA is fully live-data driven; wizard executes real actions with honest
  failure states; audit log records full context (source, target, mode) for
  failures too.

### Upgrade notes
- The container now runs as a non-root user. Volumes created by older
  releases are root-owned — run once before upgrading:
  `docker compose exec -u root queuelens chown -R queuelens /app/data`
  (fresh installs are unaffected).

### Known limitations
- Single-instance deployment only (in-memory dry-run tokens, alert state,
  env bundles) — see docs/OPERATIONS.md.
- Environment switching is instance-global (broadcast to all users).
- Alerts evaluate the active environment only.
- Front-end compiles JSX in the browser (Babel standalone); precompilation
  is on the roadmap.

## v0.5.0 and earlier

See GitHub releases.
