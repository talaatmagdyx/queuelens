# Security Policy

QueueLens reads and republishes production messages. DLQ payloads routinely contain tokens,
emails, customer data, and internal identifiers — treat every QueueLens deployment as
handling sensitive data.

## Supported versions

| Version | Supported |
|---|---|
| Latest minor release (currently 0.4.x) | ✅ |
| Older releases | ❌ — upgrade |

## Reporting a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/talaatmagdyx/queuelens/security/advisories/new)
(Security → Report a vulnerability). Do **not** open a public issue for security problems.
You can expect an initial response within a week.

## Security assumptions

QueueLens is designed to run **inside a trusted private network** (VPN, internal cluster,
or behind an authenticating reverse proxy). It is not hardened for direct public exposure:

- **Authentication is a single shared HTTP Basic Auth account.** There are no per-user
  accounts, sessions, rate limits, or lockouts. Credentials travel with every request, so
  TLS termination in front of the app is required anywhere the network isn't trusted.
- **Masking is display-only and key-based.** Values under configured keys
  (`QUEUELENS_MASKED_FIELDS`) render as `***`, but secrets under unlisted keys are shown in
  full, and replayed messages always carry the original, unmasked payload by design.
- **The audit log is advisory, not tamper-proof.** It lives in a SQLite file writable by
  the app; anyone with filesystem access can alter it. The audit identity is the shared
  Basic Auth username.
- **Anyone who can log in can read and mutate every DLQ** the configured RabbitMQ user can
  reach. There is no RBAC yet.

## Deployment guidance

- Change `QUEUELENS_ADMIN_PASSWORD` before the instance is reachable by anyone else.
- Terminate TLS in front of QueueLens; use `amqps://` and an HTTPS Management API URL when
  connecting to a remote broker.
- Give the RabbitMQ user least privilege: read on inspected queues, write on replay/park
  targets, configure only for `*.parking` queues; the Management API user needs only the
  `monitoring` tag.
- Keep `/metrics` inside the same trust boundary — it exposes queue names and volumes.
- Persist and back up the audit database if audit history matters to you
  (see [docs/OPERATIONS.md](docs/OPERATIONS.md)).

See [docs/SAFETY.md](docs/SAFETY.md) for the message-safety model (which is about not
losing messages, distinct from this document's access-security scope).
