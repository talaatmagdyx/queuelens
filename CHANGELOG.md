# Changelog

## Unreleased

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
