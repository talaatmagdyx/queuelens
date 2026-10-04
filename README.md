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

**[🌐 Website](https://talaatmagdyx.github.io/queuelens/)** ·
**[🚀 Quick start](#quick-start)** ·
**[📖 Docs](docs/)** ·
**[📦 Releases](https://github.com/talaatmagdyx/queuelens/releases)**

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
| Browsing never consumes | Non-destructive preview with requeue — read 220 messages, all 220 stay put. **Quorum queues with a delivery limit are the exception:** there every requeue counts as a delivery, so QueueLens refuses to browse them and badges them *not browsable*. RabbitMQ 4 gives every quorum queue a limit of 20 by default — to browse your quorum DLQs, lift it ([details](docs/SAFETY.md#1-browsing-never-consumes-messages)) |
| Browsing never reorders | A quorum queue puts every returned message at the *back*, so reading part of one would shuffle it. QueueLens reads quorum queues **whole** and requeues them in order, which leaves them exactly as they were. One too deep to read whole is refused, not shuffled |
| Replay can't drop messages | **Publish-before-ack**: the original is removed only *after* the broker confirms the publish. Unroutable publishes bounce back as errors, not silence |
| Bulk actions can't surprise you | A **mandatory dry-run** counts exactly what will be touched; execute runs on that exact set via a one-shot confirmation token |
| Deletes are deliberate | Explicit type-to-confirm, Admin role only |
| Everything is on the record | An *attempt* event is written before every action and an *outcome* event after — if the attempt can't be persisted, the action is refused. Every row says **who**, **with which role**, and **in which environment and vhost** |
| Ambiguity fails closed | Message fingerprints that match zero or multiple messages abort the action instead of guessing |

On RabbitMQ 4, lifting the limit on your dead-letter queues is one policy (merge
`delivery-limit` into your existing DLQ policy if you have one — only one applies per queue):

```bash
rabbitmqctl set_policy dlq-unlimited '\.dlq$' '{"delivery-limit": -1}' --apply-to quorum_queues
```

On RabbitMQ 3.x don't use `-1` — there it drops a message on its first return; remove
`x-delivery-limit` / the policy key instead.

The full safety model — each guarantee, its enforcement point, and the failure matrix —
is documented in [docs/SAFETY.md](docs/SAFETY.md).

## Light on your broker

The second rule: **an observability tool must never become the incident.** QueueLens is
deliberately lazy toward RabbitMQ:

- **The dashboard reads metadata only** — queue lists and counts from the Management API;
  message bodies are never fetched in the background
- **Browsing reads a queue once** — bodies are read only when *you* open a queue: one scan,
  down to the browse depth (`QUEUELENS_MAX_BROWSE_DEPTH`, default 5000) or 64 MiB, requeued
  straight away. Pages, search and filters are then served from that snapshot for five
  minutes — paging costs no further broker reads and no deliveries
- **No automatic payload scanning** — there is no crawler walking your queues; payload
  filters run only inside a user-initiated, size-capped bulk dry-run
- **Topology is cached** — the exchanges/bindings/queues snapshot (the most expensive
  management read) is served from a 30-second cache
- **Connection diagnostics run only on demand** — the broker test fires when you click it,
  not on a timer
- **Alerts check counts, not contents** — the evaluator reads queue statistics on a
  configurable interval (`QUEUELENS_ALERT_INTERVAL_SECONDS`, default 15s) and never
  touches message bodies
- **Auto-refresh is optional** — the dashboard's 30-second refresh can be switched off in
  General Settings; every other screen loads on navigation only

## Feature status

| Feature | Status |
|---|---|
| Safe message preview | ✅ Stable — quorum queues with a delivery limit are refused ([why](docs/SAFETY.md#1-browsing-never-consumes-messages)) |
| Deep browsing — page, search and filter the whole queue (snapshots) | ✅ Stable |
| Single-message replay / park / delete | ✅ Stable |
| Bulk operations (dry-run → execute) | ✅ Stable |
| Compressed-payload decode (gzip / deflate) | ✅ Stable |
| Multi-environment (per-env credentials) | ✅ Stable |
| Multi-vhost — chosen per browser tab, several at once | ✅ Stable |
| RBAC (Viewer / Operator / Admin) | ✅ Stable |
| Audit log + full-history export | ✅ Stable |
| Alerts — in-app notifications | ✅ Stable |
| Alerts — external delivery (email / Slack / PagerDuty / webhook) | 🧪 Experimental |
| Prometheus metrics + bundled rules | ✅ Stable |
| Alertmanager example (Slack / webhook), tested in CI | ✅ Stable |
| Replay policies (automatic retry with backoff, parking the exhausted) | 🧪 Experimental |
| Helm chart | 🧪 Experimental |
| PostgreSQL datastore | 🧪 Experimental |
| Multiple replicas (PostgreSQL, sticky sessions) | 🧪 Experimental |
| SSO behind an authenticating proxy | 🧪 Experimental |

## Features

### 🔬 Inspect
- **DLQ auto-detection** — by naming convention (`.dlq`, `_dlq`, `dead`) or by being the
  target another queue dead-letters into
- **Message X-ray** — payload (JSON / text / base64), headers, properties, routing data,
  and the parsed **`x-death` history** as a readable failure journey
- **Deep browsing** — page through the whole queue (down to the browse depth), search payloads
  and headers, filter by format or death count — all from one snapshot scan, and any
  message it shows can be replayed, parked or deleted
- **Compressed payloads decoded** — `content_encoding: gzip`/`deflate` bodies are
  transparently inflated for display (zip-bomb capped), with a toggle back to the raw
  base64 bytes; replay always publishes the original compressed body
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
  credentials**. Each browser tab picks its own environment and vhost, so switching never
  re-points anyone else, and two vhosts can be worked side by side
- **Prometheus metrics** at `/metrics` + ready-made [alert rules](deploy/prometheus/alerts.yml)

### 🛡️ Govern
- **Role-based access** — Viewer (read-only), Operator (recover), Admin (delete, config,
  users) — enforced server-side on every endpoint
- **Audit log** — filterable, with per-action durations, the acting role, environment and vhost
  on every row, and **full-history streaming export** (CSV / JSON)
- **Write-only secrets** — credentials go in through the API but never come back out;
  optional Fernet **encryption at rest**
- **Login rate limiting** — failed Basic-auth attempts are throttled per IP

## Quick start

One command, bundled broker, demo DLQs included:

```bash
git clone https://github.com/talaatmagdyx/queuelens && cd queuelens
docker compose up --build
```

The one-shot `demo` service dead-letters a few hundred messages into `payments.retry.dlq`,
`orders.created.dlq` and three more DLQs, with real `x-death` history, so there's
something to recover. Open **[http://localhost:8000/app](http://localhost:8000/app)** — sign in with
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

**Notifications** — alert fires and recoveries,
delivered in-app too.

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
| Page and search a deep DLQ, then act on any message | ❌ | ✅ |
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
- [ ] **One replica on SQLite**; more need PostgreSQL and sticky sessions ([why](docs/OPERATIONS.md#deployment-model--constraints-read-this-first))
- [ ] Scrape `/metrics` and load the bundled Prometheus alert rules

## Documentation

| Doc | What's inside |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering, components, request flows, the design decisions (fingerprints, publish-before-ack) |
| [docs/API.md](docs/API.md) | Full REST API reference — interactive OpenAPI docs also served at `/docs` |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable, precedence rules, a complete worked `.env` example |
| [docs/SAFETY.md](docs/SAFETY.md) | The safety model: every guarantee, its enforcement, the failure matrix |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deployment model & constraints, security posture, backups, troubleshooting |
| [docs/SSO.md](docs/SSO.md) | SSO behind oauth2-proxy, Authelia or an SSO ingress: identity headers, group → role mapping, trusted proxies |
| [docs/POLICIES.md](docs/POLICIES.md) | Replay policies: automatic retry with exponential backoff, parking, guard rails, the API |
| [docs/ALERTING.md](docs/ALERTING.md) | Prometheus rules → Alertmanager → Slack / webhook: wiring, secret files, tests, tuning, in-app alerts vs Alertmanager |
| [docs/KUBERNETES.md](docs/KUBERNETES.md) | Helm chart: install, required secrets, PostgreSQL, ingress/TLS, ServiceMonitor, sidecar proxy, more than one replica, upgrades |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, test strategy, front-end pipeline, release checklist |

## Development

```bash
python -m pip install '.[dev]'
ruff check app tests && mypy app && pytest -q
```

Fully async stack: FastAPI + aio-pika (9 or 10) + httpx + SQLAlchemy asyncio. CI runs lint,
`mypy --strict`, the unit suite with coverage, integration tests against real RabbitMQ
**3.13 and 4.1**, a black-box acceptance run of every feature group (200+ checks, also on
both brokers), a Playwright browser e2e suite, CodeQL, and the Docker build — on every push.
See [CONTRIBUTING.md](CONTRIBUTING.md) to get started; good first issues are labeled.

## Honest limitations

- One replica on SQLite. On PostgreSQL several can run, but browse snapshots stay on the
  replica that took them, so they need sticky sessions; without them paging rescans.
- Filter-based bulk operations act on the scan window (up to `QUEUELENS_MAX_BULK_SIZE` from
  the head of the queue). Messages picked from a snapshot can be reached down to the browse
  depth (default 5000)
- Quorum DLQs are browsed only whole, to keep their order, so one deeper than the browse
  depth isn't browsable until it drains or the depth is raised
- Masking is key-based and display-only — it will not detect secrets under unlisted keys
- Message fingerprints are best-effort identifiers, not global message IDs; ambiguous
  matches fail safely

## License

MIT — see [LICENSE](LICENSE). Built for the on-call engineer who deserves better than
`rabbitmqadmin get` at 3 a.m.
