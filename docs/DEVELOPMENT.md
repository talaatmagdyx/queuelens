# Development Guide

## Setup

Requires Python 3.12+ and (for integration tests / manual runs) Docker.

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install '.[dev]'
```

Run the full local gate — this is what CI runs:

```bash
ruff check app tests   # lint (line length 100, rules E/F/I/UP/B)
mypy app               # strict mode
pytest -q              # unit + route tests; integration test auto-skips without a broker
```

Run the app against the compose broker:

```bash
docker compose up -d rabbitmq
QUEUELENS_RABBITMQ_URL=amqp://queuelens:queuelens@localhost:5672/ \
QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://localhost:15672 \
QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME=queuelens \
QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=queuelens \
uvicorn app.main:app --reload
```

Or the whole stack: `docker compose up --build`.

## Test strategy

Two layers, both required:

1. **Unit/route tests** (`tests/test_*.py`) — fast, no broker. Services are swapped on
   `app.state` (`app.state.message_service = FakeMessageService()`); AMQP channels are
   replaced with in-memory fakes (see `tests/test_actions.py`).
2. **Real-broker integration test** (`tests/test_integration_rabbitmq.py`) — the full
   browse → failed-replay → park → replay-move → delete → audit journey against live
   RabbitMQ. Auto-skips when no broker is reachable; override the target with
   `QUEUELENS_IT_AMQP_URL` / `QUEUELENS_IT_MANAGEMENT_URL`.

**Rule of thumb:** any change to publish/ack ordering, target verification, fingerprinting,
or requeue behavior needs an integration-test assertion, not just a fake-based unit test.
Fakes have already lied to us once — see the integration test's module docstring.

Conventions: `pytest-asyncio` in auto mode (plain `async def` tests), fresh `create_app(...)`
per test with explicit `Settings`, `tmp_path` SQLite URLs for anything touching audit.

## CI

`.github/workflows/ci.yml`, on every push/PR:

- **test** job — ruff, mypy, pytest with a `rabbitmq:3.13-management` service container
  (credentials match the integration test defaults, so nothing to configure).
- **docker** job — image build.

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
- **Frontend** — server-rendered Jinja2 + a small amount of vanilla JS in the templates.
  Build DOM nodes (`textContent`), never `innerHTML` with server data.

## Adding an endpoint (checklist)

1. Domain model / service method with types.
2. Route in `app/api/routes/` (and `app/web/routes.py` if it has a page), resolving services
   from `app.state`.
3. Error mapping: does an existing handler cover the failure modes? If not, extend
   `_register_error_handlers`.
4. Unit test with fakes + integration assertion if it touches the broker.
5. Update [API.md](API.md) and, if behavior-relevant, [SAFETY.md](SAFETY.md).

## Release

```bash
ruff check app tests && mypy app && pytest -q   # green gate, broker running
git tag -a vX.Y.Z -m "…release notes…"
git push --tags
```

Version lives in `pyproject.toml` (and `create_app`'s `version=`) — keep them in sync with
the tag.
