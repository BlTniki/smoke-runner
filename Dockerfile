# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.8.15 AS uv

FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:${PATH}"

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --gid 10001 smoke-runner \
    && useradd --uid 10001 --gid smoke-runner --no-create-home smoke-runner \
    && mkdir -p /data \
    && chown smoke-runner:smoke-runner /data

USER 10001:10001

CMD ["python", "-m", "smoke_runner"]
