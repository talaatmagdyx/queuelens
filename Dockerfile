# ── Stage 1: precompile the SPA (JSX → JS, drop Babel standalone) ──────────
FROM node:20-slim AS frontend
WORKDIR /build
COPY app/web/static ./static
COPY scripts/build_frontend.mjs scripts/
RUN npm install --no-save --no-audit --no-fund @babel/core@7 @babel/preset-react@7 \
    && node scripts/build_frontend.mjs static

# ── Stage 2: the application image ─────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts/build_ds_bundle.py scripts/
COPY deploy/prometheus ./deploy/prometheus
# precompiled SPA replaces the raw JSX tree
COPY --from=frontend /build/static ./app/web/static
RUN pip install --no-cache-dir . \
    && python scripts/build_ds_bundle.py \
    && mkdir -p /app/data \
    && useradd --system --no-create-home queuelens \
    && chown -R queuelens /app/data

USER queuelens
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
