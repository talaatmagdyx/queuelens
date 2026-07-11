# Contributing to QueueLens

Thanks for helping make DLQ recovery safer.

## Development setup

```bash
git clone https://github.com/talaatmagdyx/queuelens && cd queuelens
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
docker compose up -d rabbitmq mailpit   # broker + email sink
.venv/bin/uvicorn app.main:app --reload # or: docker compose up --build queuelens
```

## Before you open a PR

All three gates must pass — CI enforces them:

```bash
.venv/bin/ruff check app tests
.venv/bin/mypy app
.venv/bin/pytest -q
```

- Every new endpoint needs a test; every bug fix needs a regression test.
- Destructive operations must be confirm-gated, audited, and publish-before-ack.
  Read `docs/SAFETY.md` first — the safety invariants are non-negotiable.
- Secrets must never appear in API responses (see the redaction patterns in
  `app/api/routes/platform.py`).
- The SPA (`app/web/static/ds/ui_kits/queuelens/`) renders live data only —
  no hardcoded sample values. Bump the `?v=` cache param in `index.html`
  whenever you touch a `.js`/`.jsx` file.

## Reporting security issues

Please follow [SECURITY.md](SECURITY.md) — do not open public issues for
vulnerabilities.
