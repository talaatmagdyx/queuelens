# Development Guide

## Setup

Requires Python 3.12+ and (for integration tests / manual runs) Docker.

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install '.[dev]'
```

Run the local gate before a pull request: `make` (or `make all`). It runs:
- `ruff` lint, line length 100, rules E/F/I/UP/B
- `mypy`, strict
- `pytest`: the RabbitMQ integration tests run when the compose broker is up, and skip otherwise
- the frontend precompile, as the Docker build does it
- the alerting pipeline: promtool, amtool, and a real Alertmanager delivery
- the Helm chart render, with kubeconform

The other CI jobs each have a target. Each one starts the throwaway services it needs and
stops them when it ends:

| Target | Runs | Needs |
|---|---|---|
| `make acceptance` | every feature end to end, against the compose broker and two Mailpits | Docker |
| `make e2e` | the browser smoke test, against a QueueLens on port 8123 | Docker, Playwright |
| `make test-postgres` | the test suite on PostgreSQL 17 as well (`PG_PORT`, default 55499) | Docker |
| `make helm-kind` | the chart in a throwaway kind cluster: one replica, then two on PostgreSQL | Docker, kind, kubectl |
| `make screenshots` | `docs/screenshots/`, regenerated against a throwaway RabbitMQ | Docker, Playwright |

`make help` lists them all. Set `VENV` if your virtualenv isn't `.venv`. The targets keep their
working files in `.cache/` (git-ignored).

Run the app against the compose broker:

```bash
docker compose up -d rabbitmq
QUEUELENS_RABBITMQ_URL=amqp://queuelens:queuelens@localhost:5672/ \
QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://localhost:15672 \
QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME=queuelens \
QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=queuelens \
uvicorn app.main:app --reload
```

Or the whole stack: `docker compose up --build`. Its one-shot `demo` service runs
`python -m app.demo`, which dead-letters realistic messages into a handful of DLQs (real
`x-death`, some messages that died several times, gzip bodies, a quorum DLQ with a delivery
limit). It is idempotent.

The README and landing-page screenshots come from `make screenshots`, which runs
`scripts/screenshots.py` against a throwaway broker (needs `pip install playwright`). It seeds the demo data, starts its own QueueLens, creates some
history through the API, and captures every screen at 1440×900 into `docs/screenshots/`.
Point it at a broker with nothing else on it (`QUEUELENS_RABBITMQ_URL`,
`QUEUELENS_RABBITMQ_MANAGEMENT_URL`): other queues show up in the shots.

## Test strategy

Four layers:

1. **Unit/route tests** (`tests/test_*.py`) — fast, no broker. Services are swapped on
   `app.state` (`app.state.message_service = FakeMessageService()`); AMQP channels are
   replaced with in-memory fakes (see `tests/test_actions.py`).
2. **Real-broker integration test** (`tests/test_integration_rabbitmq.py`) — the full
   browse → failed-replay → park → replay-move → delete → audit journey against live
   RabbitMQ, plus the quorum delivery-limit guard on whatever broker version it runs
   against. Auto-skips when no broker is reachable; override the target with
   `QUEUELENS_IT_AMQP_URL` / `QUEUELENS_IT_MANAGEMENT_URL`.
3. **Browser smoke** (`tests/e2e/`, `E2E=1`) — Playwright against a running instance.
4. **Black-box acceptance run** (`tests/acceptance/run.py`, `ACCEPTANCE=1`) — boots
   QueueLens itself (auth on), seeds a real broker, and runs ~200 checks across every
   feature group through the HTTP API: dead-lettering with real `x-death`, replay/park/
   delete/bulk failure modes, RBAC, audit-store outages, alert delivery (incl. retries,
   quiet hours, STARTTLS against an untrusted certificate), environment switching,
   restarts. It is a script, not a pytest module (it owns the server lifecycle); it exits
   non-zero on any FAIL. It needs a **disposable** broker and two Mailpits — the commands
   are in its module docstring. This is the layer that found the quorum message-loss and
   unaudited-bulk bugs; extend it when you add a feature, especially one with a failure
   mode.

**Rule of thumb:** any change to publish/ack ordering, target verification, fingerprinting,
or requeue behavior needs an integration-test assertion, not just a fake-based unit test.
Fakes have already lied to us once — see the integration test's module docstring.

Conventions: `pytest-asyncio` in auto mode (plain `async def` tests), fresh `create_app(...)`
per test with explicit `Settings`, `tmp_path` SQLite URLs for anything touching audit.
`tests/test_databases.py` also runs the persistence layer on PostgreSQL when
`QUEUELENS_TEST_POSTGRES_URL` names a throwaway database (its tables are dropped).
`tests/test_replicas.py` then runs two or three app instances on it as replicas, and
the integration suite has two replicas scan one real queue at once.
`ACCEPTANCE_DATABASE_URL` runs the acceptance suite on PostgreSQL too. CI sets both:

```bash
docker run -d --rm --name ql-pg -e POSTGRES_HOST_AUTH_METHOD=trust -p 5433:5432 postgres:17
QUEUELENS_TEST_POSTGRES_URL=postgresql+asyncpg://postgres@127.0.0.1:5433/postgres pytest -q tests/test_databases.py
```

## CI

`.github/workflows/ci.yml`, on every push/PR:

- **test** job — ruff, mypy, pytest against `rabbitmq:3.13-management` **and**
  `rabbitmq:4.1-management` (matrix; credentials match the integration test defaults).
- **e2e** job — the browser smoke against a booted instance.
- **acceptance** job — the black-box run on both broker versions; results and the server
  log are uploaded as the `acceptance-<version>` artifact.
- **docker** job — image build.
- **publish** (tags only) — multi-arch images to GHCR, only after test, docker and
  acceptance pass.
- **publish-chart** (tags only) — the Helm chart to `oci://ghcr.io/talaatmagdyx/charts`,
  after publish and the helm job. It fails when Chart.yaml's `appVersion` isn't the tag or
  its `version` is already published.

Also on every PR: **CodeQL** (`codeql.yml` — Python, the console's JavaScript, and the
workflows themselves; vendored minified libraries are excluded), results under
Security → Code scanning. **Dependabot** (`dependabot.yml`) proposes grouped weekly updates
for Python packages, GitHub Actions and the Docker base images (the Python/Node runtime
line is bumped by hand); security fixes arrive immediately.

## Code conventions

- **Layering** — routes → application services → infrastructure → domain. Routes resolve
  services from `request.app.state` and never import infrastructure directly.
- **Typing** — `mypy --strict` is a hard gate. Domain objects are frozen slotted dataclasses.
- **Errors** — raise domain/infrastructure exceptions and map them centrally
  (`_register_error_handlers` in `app/main.py`); don't catch-and-convert inside services.
  The exception: action routes convert to HTTP errors inline so the failure can be audited.
- **Safety first** — any new mutation must (1) audit `started` before executing,
  (2) publish before ack, (3) verify the target exists or use mandatory publish,
  (4) requeue everything on failure. Read [SAFETY.md](SAFETY.md) before touching
  `MessageOperator`.
- **Frontend** — the React SPA at `/app` (see the front-end pipeline section below);
  the only remaining Jinja template is the standalone error page.
  Build DOM nodes (`textContent`), never `innerHTML` with server data.

## Adding an endpoint (checklist)

1. Domain model / service method with types.
2. Route in `app/api/routes/` (and `app/web/routes.py` if it has a page), resolving services
   from `app.state`.
3. Error mapping: does an existing handler cover the failure modes? If not, extend
   `_register_error_handlers`.
4. Unit test with fakes + integration assertion if it touches the broker.
5. Update [API.md](API.md) and, if behavior-relevant, [SAFETY.md](SAFETY.md).

## Adding a database column

`create_all` creates missing tables but never alters existing ones. So a column added to a
table that an earlier release shipped also goes in `Database.MIGRATIONS`, in
`app/infrastructure/persistence/database.py`, as `(table, column, SQL type)`. Make the
column nullable, or give it a default, so it can be added to a table that already has rows.

At startup, QueueLens adds any listed column the database lacks. It does this inside the
schema lock, so replicas starting together don't race. If a column can't be added, QueueLens
doesn't start, and the error names the column and the `ALTER TABLE` to run.

`tests/test_databases.py` upgrades a database that lacks every listed column, on SQLite and
PostgreSQL. It also fails when a table has a column that isn't in `tests/schema_released.json`
(the last release's schema) and isn't listed either: that's a forgotten migration. When a
release adds a table, refresh that file.

## Release

```bash
ruff check app tests && mypy app && pytest -q   # green gate, broker running
git tag -a vX.Y.Z -m "…release notes…"
git push --tags
```

If the release adds a table, refresh `tests/schema_released.json` (see
[Adding a database column](#adding-a-database-column)).

Version lives in `pyproject.toml` (and `create_app`'s `version=`) — keep them in sync with
the tag. So does the chart's `appVersion` in `deploy/helm/queuelens/Chart.yaml`, whose own
`version` goes up with every release (a published chart version is never replaced). GHCR
creates the chart package private: after the first chart release, make `charts/queuelens`
public in its package settings, as for the image.


## Front-end pipeline

In development the SPA compiles JSX in the browser (Babel standalone) so you
can edit `.jsx` files and refresh. The production image precompiles everything:
`scripts/build_frontend.mjs` (node stage) compiles the ui-kit and the inline
shell and removes Babel; `scripts/build_ds_bundle.py` emits
`_ds_bundle.js`, which `ds-loader.js` prefers over evaluating raw component
sources. Remember to bump the `?v=` cache parameter in `index.html` whenever
you touch front-end files.
