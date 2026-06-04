# Backend API image. Dependencies are installed with **uv** straight from the
# committed uv.lock (fast, reproducible) instead of pip. The BuildKit cache
# mount keeps uv's download cache across builds, so heavy wheels (torch, mineru)
# download once and are reused — including by the worker image (Dockerfile.mineru).
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
    UV_CONCURRENT_DOWNLOADS=4

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

# Resolve the exact locked deps to a hashed requirements file, then install them
# into the system environment. `--mount=type=cache` persists uv's wheel cache
# between builds (this is the pip -> uv download speedup).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.lock \
 && uv pip install --system \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      --index-strategy unsafe-best-match \
      -r /tmp/requirements.lock

COPY app ./app

# Compose overrides this for the worker; default to the API.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
