# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

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
    && mkdir -p /var/lib/trend-sift/logs \
    && chown -R trend-sift:trend-sift /var/lib/trend-sift
COPY --from=builder --chown=trend-sift:trend-sift /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TREND_SIFT_DB_PATH=/var/lib/trend-sift/trending.db \
    TREND_SIFT_LOG_DIR=/var/lib/trend-sift/logs
USER trend-sift
WORKDIR /var/lib/trend-sift
ENTRYPOINT ["trend-sift"]
CMD ["--help"]
