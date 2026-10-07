# Shortcuts for the checks CI runs. `make` (that is, `make all`) is the gate before a pull
# request. The targets after it start the throwaway services they need and stop them
# afterwards. `make help` lists every target.

VENV ?= .venv
PY := $(VENV)/bin/python
CACHE := .cache
PG_PORT ?= 55499

.PHONY: all lint types test frontend alerting helm acceptance e2e test-postgres helm-kind screenshots help

all: lint types test frontend alerting helm ## lint, types, tests, frontend build, alert rules, Helm render

lint: ## ruff
	$(VENV)/bin/ruff check .

types: ## mypy, strict
	$(VENV)/bin/mypy app

test: ## unit and route tests (the RabbitMQ ones also run when the compose broker is up)
	$(VENV)/bin/pytest -q

frontend: $(CACHE)/frontend/node_modules ## precompile the console as the Docker build does, into a copy
	rm -rf $(CACHE)/frontend/static
	cp -R app/web/static $(CACHE)/frontend/static
	cp scripts/build_frontend.mjs $(CACHE)/frontend/
	cd $(CACHE)/frontend && node build_frontend.mjs static  # Babel finds its presets from here

$(CACHE)/frontend/node_modules:
	mkdir -p $(CACHE)/frontend
	cd $(CACHE)/frontend && npm init -y >/dev/null && \
	  npm install --no-save --no-audit --no-fund @babel/core@7 @babel/preset-react@7

alerting: ## promtool, amtool and a real Alertmanager delivery (Docker)
	$(PY) scripts/test_alerting.py

helm: ## lint and render the chart, validate the manifests with kubeconform (Docker)
	scripts/test_helm_chart.sh render

helm-kind: ## install the chart in a throwaway kind cluster: one replica, then two on PostgreSQL
	scripts/test_helm_chart.sh kind kind-replicas

acceptance: ## every feature end to end, on the compose broker with two Mailpits (Docker)
	docker compose up -d --wait rabbitmq
	@# the second Mailpit makes its own self-signed certificate (sans:), so no openssl here
	@trap 'docker stop ql-mail ql-mail-tls >/dev/null 2>&1' EXIT; \
	docker run -d --rm --name ql-mail -p 1025:1025 -p 8025:8025 axllent/mailpit >/dev/null && \
	docker run -d --rm --name ql-mail-tls -p 1026:1025 -p 8027:8025 axllent/mailpit \
	  --smtp-tls-cert sans:untrusted.invalid --smtp-tls-key sans:untrusted.invalid \
	  --smtp-require-starttls >/dev/null && \
	for i in $$(seq 30); do curl -sf localhost:8025/api/v1/info >/dev/null && \
	  curl -sf localhost:8027/api/v1/info >/dev/null && break; sleep 1; done && \
	ACCEPTANCE=1 $(PY) tests/acceptance/run.py

e2e: ## the browser smoke test, against a QueueLens on the compose broker (Docker, Playwright)
	docker compose up -d --wait rabbitmq
	$(PY) -m playwright install chromium
	@mkdir -p $(CACHE); rm -f $(CACHE)/e2e.db; \
	QUEUELENS_AUTH_ENABLED=false QUEUELENS_DATABASE_URL=sqlite+aiosqlite:///$(CACHE)/e2e.db \
	  QUEUELENS_RABBITMQ_URL=amqp://queuelens:queuelens@localhost:5672/ \
	  QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://localhost:15672 \
	  QUEUELENS_RABBITMQ_MANAGEMENT_USERNAME=queuelens QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD=queuelens \
	  $(PY) -m uvicorn app.main:app --port 8123 >$(CACHE)/e2e.log 2>&1 & server=$$!; \
	trap 'kill $$server 2>/dev/null; wait $$server 2>/dev/null' EXIT; \
	for i in $$(seq 30); do curl -sf localhost:8123/ready >/dev/null && break; sleep 1; done && \
	E2E=1 $(VENV)/bin/pytest tests/e2e -q

test-postgres: ## the test suite on PostgreSQL 17 as well, as CI runs it (Docker)
	@trap 'docker stop ql-test-pg >/dev/null 2>&1' EXIT; \
	docker run -d --rm --name ql-test-pg -e POSTGRES_HOST_AUTH_METHOD=trust \
	  -p $(PG_PORT):5432 postgres:17 >/dev/null && \
	for i in $$(seq 30); do docker exec ql-test-pg pg_isready -h 127.0.0.1 -q && break; sleep 1; done && \
	QUEUELENS_TEST_POSTGRES_URL=postgresql+asyncpg://postgres@localhost:$(PG_PORT)/postgres \
	  $(VENV)/bin/pytest -q

screenshots: ## regenerate docs/screenshots/ against a throwaway RabbitMQ (Docker, Playwright)
	@trap 'docker stop ql-shots >/dev/null 2>&1' EXIT; \
	docker run -d --rm --name ql-shots -e RABBITMQ_DEFAULT_USER=queuelens \
	  -e RABBITMQ_DEFAULT_PASS=queuelens -p 5674:5672 -p 15674:15672 rabbitmq:3.13-management >/dev/null && \
	for i in $$(seq 90); do curl -sf -u queuelens:queuelens localhost:15674/api/overview >/dev/null && \
	  break; sleep 1; done && sleep 4 && \
	QUEUELENS_RABBITMQ_URL=amqp://queuelens:queuelens@localhost:5674/ \
	  QUEUELENS_RABBITMQ_MANAGEMENT_URL=http://localhost:15674 $(PY) scripts/screenshots.py

help: ## list the targets
	@awk -F ':.*## ' '/^[a-z-]+:.*## /{printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
