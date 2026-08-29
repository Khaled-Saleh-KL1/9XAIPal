# Backend API image (local dev). Dependencies come from pyproject.toml/
# uv.lock's `mineru` extra — the same lockfile Dockerfile.mineru and
# Dockerfile.lite use, just a different extra selected. The BuildKit cache
# mount keeps uv's download cache across builds, so heavy wheels (torch,
# mineru) download once and are reused across all three images.
#
# The Celery worker uses Dockerfile.mineru, which adds the OpenCV/runtime libs
# and pre-downloads the MinerU pipeline models.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    # The locked torch pulls in multi-hundred-MB CUDA wheels. The default 30s
    # HTTP timeout aborts mid-download on a slow link, so raise it and cap how
    # many huge wheels download in parallel (so each gets enough bandwidth).
    UV_HTTP_TIMEOUT=600 \
    UV_CONCURRENT_DOWNLOADS=4 \
    # Install into the system interpreter below, not a project-local .venv —
    # keeps `CMD`/`uvicorn` working unprefixed, same as `uv pip install
    # --system` did before.
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# uv binary, pinned to the same version that produced the lockfile.
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/

# System deps for asyncpg/psycopg2 builds + the healthcheck's curl.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# `--mount=type=cache` persists uv's wheel cache between builds; `--locked`
# fails the build instead of silently re-resolving if uv.lock and
# pyproject.toml ever disagree (e.g. someone edited one by hand).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --extra mineru

COPY app ./app

# Compose overrides this for the worker; default to the API.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
