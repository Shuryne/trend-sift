# syntax=docker/dockerfile:1
FROM node:22-slim AS frontend
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm run build

FROM ghcr.io/astral-sh/uv:0.12.15 AS uv

FROM python:3.14-slim AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim AS runtime
RUN groupadd --system --gid 10001 trend-sift \
    && useradd --system --uid 10001 --gid trend-sift --home-dir /nonexistent trend-sift \
    && mkdir -p /var/lib/trend-sift /var/log/trend-sift \
    && chown -R trend-sift:trend-sift /var/lib/trend-sift /var/log/trend-sift
COPY --from=builder --chown=trend-sift:trend-sift /app/.venv /app/.venv
COPY --from=frontend --chown=trend-sift:trend-sift /web/dist /app/web/dist
ENV TREND_SIFT_WEB_DIR=/app/web/dist \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TREND_SIFT_DB_PATH=/var/lib/trend-sift/trending.db \
    TREND_SIFT_LOG_DIR=/var/log/trend-sift
WORKDIR /var/lib/trend-sift
COPY deploy/docker-entrypoint.py /app/docker-entrypoint.py
ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
CMD ["uvicorn", "trend_sift.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
