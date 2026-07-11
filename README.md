<div align="center">

# 🔍 QueueLens

**See inside your dead-letter queues. Recover messages without fear.**

[![CI](https://github.com/talaatmagdyx/queuelens/actions/workflows/ci.yml/badge.svg)](https://github.com/talaatmagdyx/queuelens/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/talaatmagdyx/queuelens)](https://github.com/talaatmagdyx/queuelens/releases)
[![Docker](https://img.shields.io/badge/ghcr.io-queuelens-blue?logo=docker)](https://github.com/talaatmagdyx/queuelens/pkgs/container/queuelens)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

*An async RabbitMQ DLQ inspector with safe replay — browse, understand, replay, park,
and delete dead-lettered messages without ever losing one.*

![QueueLens Dashboard](docs/screenshots/dashboard.png)

</div>

---

## The 3 a.m. problem

A deploy goes out. An hour later, `payments.retry.dlq` has 220 messages in it and
someone is paging you. Now you need answers, fast:

- **What died?** Which payloads, from which exchange, rejected how many times, and why?
- **Can I put it back?** Replay to the original queue — but *only* the ones that are safe.
- **What about the rest?** Park the poison messages somewhere they can't hurt anyone.
- **Who touched what?** When the incident review comes, you need a paper trail.

The RabbitMQ Management UI shows you queue *counts*. Your options beyond that are
`rabbitmqadmin get` (which **consumes the message while you look at it**) or a one-off
Python script written at 3 a.m. by someone whose hands are shaking.

QueueLens is the tool you wish you'd installed before the incident:

```text
   inspect safely  →  understand the failure  →  act deliberately  →  audit everything
  (never consumes)    (parsed x-death, journey)   (replay / park /     (attempt + outcome,
                                                   delete, dry-run      full export)
                                                   first, confirmed)
```

## Why it's safe — the part that matters

Every design decision follows one rule: **a failed action must never lose a message.**

| Guarantee | How it's enforced |
|---|---|
| Browsing never consumes | Non-destructive preview with requeue — read 220 messages, all 220 stay put |
| Replay can't drop messages | **Publish-before-ack**: the original is removed only *after* the broker confirms the publish. Unroutable publishes bounce back as errors, not silence |
| Bulk actions can't surprise you | A **mandatory dry-run** counts exactly what will be touched; execute runs on that exact set via a one-shot confirmation token |
| Deletes are deliberate | Explicit type-to-confirm, Admin role only |
| Everything is on the record | An *attempt* event is written before every action and an *outcome* event after — if the attempt can't be persisted, the action is refused |
| Ambiguity fails closed | Message fingerprints that match zero or multiple messages abort the action instead of guessing |

The full safety model — each guarantee, its enforcement point, and the failure matrix —
is documented in [docs/SAFETY.md](docs/SAFETY.md).

## Features

### 🔬 Inspect
- **DLQ auto-detection** — by naming convention (`.dlq`, `_dlq`, `dead`) or by being the
  target another queue dead-letters into
- **Message X-ray** — payload (JSON / text / base64), headers, properties, routing data,
  and the parsed **`x-death` history** as a readable failure journey
- **Risk-sorted dashboard** — the queues most likely to be your problem float to the top
- **Topology view** — which queues dead-letter into which, as a graph, not a hunch
- **Sensitive-field masking** — `password`, `token`, `email`, … render as `***`
  (display-only; replayed payloads are never modified)

### ⚡ Act
- **Replay (copy or move)** — to a queue or an exchange + routing key, with
  `x-queuelens-*` provenance headers stamped on every replayed message
- **Park** — quarantine a message to `{queue}.parking` (created on demand)
- **Bulk operations** — replay/park/delete many at once, scoped by selection or payload
  filter, dry-run first, hard caps, per-message results
- **Test message composer** — publish a crafted message to reproduce a failure

### 🚨 Operate
- **Alert rules** — pattern-match queues (`payments.*`), threshold + duration, with
  fire *and* recovery notifications
- **Delivery channels** — email (SMTP/TLS), Slack, PagerDuty (Events API v2), and generic
  webhooks — all with 3-attempt backoff retry and per-channel outcome tracking
- **Quiet hours** — mute Info/Warning notifications overnight; real alerts always deliver
- **Multiple environments** — development / staging / production brokers with **per-environment
  credentials**, switchable from the UI
- **Prometheus metrics** at `/metrics` + ready-made [alert rules](deploy/prometheus/alerts.yml)

### 🛡️ Govern
- **Role-based access** — Viewer (read-only), Operator (recover), Admin (delete, config,
  users) — enforced server-side on every endpoint
- **Audit log** — filterable, with per-action durations, and **full-history streaming
  export** (CSV / JSON)
- **Write-only secrets** — credentials go in through the API but never come back out;
  optional Fernet **encryption at rest**
- **Login rate limiting** — failed Basic-auth attempts are throttled per IP

## Quick start

One command, bundled broker, demo DLQs included:

```bash
git clone https://github.com/talaatmagdyx/queuelens && cd queuelens
docker compose up --build
```

Open **[http://localhost:8000/app](http://localhost:8000/app)** — sign in with
`admin` / `change-me` (change it before sharing the URL with anyone).
The bundled RabbitMQ Management UI is at [http://localhost:15672](http://localhost:15672)
(`queuelens` / `queuelens`).

## Point it at your cluster

Prebuilt multi-stage images (non-root, precompiled UI, health-checked) are published to
GHCR on every release:

```bash
docker run --rm -p 8000:8000 \
  -e QUEUELENS_RABBITMQ_URL='amqps://user:pass@rabbitmq.internal:5671/' \
  -e QUEUELENS_RABBITMQ_MANAGEMENT_URL='https://rabbitmq.internal:15671' \
  -e QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME='monitoring-user' \
  -e QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD='…' \
  -e QUEUELENS_ADMIN_USERNAME='admin' \
  -e QUEUELENS_ADMIN_PASSWORD='change-me-now' \
  -e QUEUELENS_SECRET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  -v queuelens-data:/app/data \
  ghcr.io/talaatmagdyx/queuelens:latest
```

Every `QUEUELENS_*` variable — including multi-environment JSON, alert channels, and
least-privilege broker permissions — is documented with a full worked example in
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## A tour

<table>
<tr>
<td width="50%">

**Message X-ray** — payload, headers, and the
parsed `x-death` journey, with replay / park /
delete one deliberate click away.

![Message detail](docs/screenshots/message-detail.png)

</td>
<td width="50%">

**Replay wizard** — pick the action, pick the
target, see exactly what will happen. Execution
stays locked until you confirm.

![Replay wizard](docs/screenshots/replay-wizard.png)

</td>
</tr>
<tr>
<td>

**Alerts** — rules that watch queue patterns and
deliver to email, Slack, PagerDuty, or webhooks —
with retries and recovery notifications.

![Alerts](docs/screenshots/alerts.png)

</td>
<td>

**Audit log** — every attempt and outcome, with
durations, filters, and full-history CSV/JSON
export for the incident review.

![Audit log](docs/screenshots/audit.png)

</td>
</tr>
<tr>
<td>

**Topology** — the dead-letter graph of your
broker: who routes failures where.

![Topology](docs/screenshots/topology.png)

</td>
<td>

**Configuration** — brokers, environments with
per-env credentials, delivery channels, limits —
all managed from the UI, secrets write-only.

![Configuration](docs/screenshots/configuration.png)

</td>
</tr>
<tr>
<td>

**Dark mode** — one toggle, persisted per browser.

![Dashboard dark](docs/screenshots/dashboard-dark.png)

</td>
<td>

**Notifications** — alert fires, recoveries, and
environment switches, delivered in-app too.

![Notifications](docs/screenshots/notifications.png)

</td>
</tr>
</table>

## QueueLens vs. RabbitMQ Management UI

QueueLens is a **focused DLQ recovery tool**, not a Management UI replacement — the
Management UI manages the broker; QueueLens recovers your messages.

| Capability | Management UI | QueueLens |
|---|:---:|:---:|
| Queue counts & broker admin | ✅ | read-only |
| Browse messages without consuming them | ⚠️ requeue quirks | ✅ |
| Parsed `x-death` failure history | raw headers | ✅ |
| Safe replay (publish-before-ack) | manual & risky | ✅ |
| Bulk actions with mandatory dry-run | ❌ | ✅ |
| Attempt + outcome audit trail | ❌ | ✅ |
| Alert rules → email / Slack / PagerDuty | ❌ | ✅ |
| Role-based access (Viewer / Operator / Admin) | broker perms only | ✅ |
| Sensitive-field display masking | ❌ | ✅ |

## Before production

The short version (the full checklist lives in [docs/OPERATIONS.md](docs/OPERATIONS.md)):

- [ ] Change `QUEUELENS_ADMIN_PASSWORD` and set `QUEUELENS_SECRET_KEY` (secrets-at-rest encryption)
- [ ] Run behind a VPN or authenticating reverse proxy with TLS — never expose it publicly
- [ ] Use a least-privilege broker user (read DLQs, write replay targets, configure only `*.parking`)
- [ ] Persist `/app/data` on a volume and back it up if audit history matters
- [ ] **Run a single replica** — environment switching and SQLite are single-process by design
- [ ] Scrape `/metrics` and load the bundled Prometheus alert rules

## Documentation

| Doc | What's inside |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering, components, request flows, the design decisions (fingerprints, publish-before-ack) |
| [docs/API.md](docs/API.md) | Full REST API reference — interactive OpenAPI docs also served at `/docs` |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable, precedence rules, a complete worked `.env` example |
| [docs/SAFETY.md](docs/SAFETY.md) | The safety model: every guarantee, its enforcement, the failure matrix |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deployment model & constraints, security posture, backups, troubleshooting |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, test strategy, front-end pipeline, release checklist |

## Development

```bash
python -m pip install '.[dev]'
ruff check app tests && mypy app && pytest -q
```

Fully async stack: FastAPI + aio-pika + httpx + SQLAlchemy asyncio. CI runs lint,
`mypy --strict`, the unit suite with coverage, an integration flow against a real
RabbitMQ container, a Playwright browser e2e suite, and the Docker build — on every push.
See [CONTRIBUTING.md](CONTRIBUTING.md) to get started; good first issues are labeled.

## Honest limitations

- Single replica by design (SQLite + instance-global environment switching) —
  PostgreSQL and per-request env scoping are the path to multi-replica, on the roadmap
- Bulk operations act on the scan window (up to `QUEUELENS_MAX_BULK_SIZE` from the head
  of the queue), not the whole queue
- Masking is key-based and display-only — it will not detect secrets under unlisted keys
- Message fingerprints are best-effort identifiers, not global message IDs; ambiguous
  matches fail safely

## License

MIT — see [LICENSE](LICENSE). Built for the on-call engineer who deserves better than
`rabbitmqadmin get` at 3 a.m.
