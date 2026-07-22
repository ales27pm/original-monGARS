# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.30@sha256:93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra documents --no-install-project

COPY README.md ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra documents --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001
ARG SECRET_GID=1000

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" mongars \
    && useradd --uid "${APP_UID}" --gid mongars --create-home --home-dir /home/mongars mongars \
    && if [ "${SECRET_GID}" != "${APP_GID}" ]; then \
         groupadd --gid "${SECRET_GID}" mongars-secrets; \
         usermod --append --groups mongars-secrets mongars; \
       fi \
    && install --directory --owner=mongars --group=mongars /var/lib/mongars

WORKDIR /app

COPY --from=build --chown=mongars:mongars /app/.venv /app/.venv
COPY --chown=mongars:mongars alembic.ini ./
COPY --chown=mongars:mongars migrations ./migrations

USER mongars

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/healthz', timeout=2).read()"]

STOPSIGNAL SIGTERM

CMD ["uvicorn", "mongars.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
