"""FastAPI dependencies."""

import os
from typing import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import async_session_factory
from app.core.config import Settings, settings


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a database session per request."""
    async with async_session_factory() as session:
        yield session


def get_settings() -> Settings:
    """Return app settings."""
    return settings


# ------------------------------------------------------------------
# Lightweight concurrency limiter for the expensive /ask path.
# On "your computer = server" with multiple users (or even one user with
# many tabs/sub-threads + research), we do not want 10+ simultaneous
# LLM calls (router + research rounds + synthesis) all hitting the same
# Ollama instance at once. This causes OOM, extreme latency, or GPU
# thrashing.
#
# Each uvicorn worker gets its own semaphore (with --workers 2 this gives
# reasonable headroom). Excess requests are queued by the semaphore.
# Tune via MAX_CONCURRENT_ASKS in the environment / .env.
# ------------------------------------------------------------------
import asyncio
from typing import AsyncIterator

_max_concurrent_asks = int(os.getenv("MAX_CONCURRENT_ASKS", "3"))
_ask_semaphore = asyncio.Semaphore(_max_concurrent_asks)


async def get_ask_limiter() -> AsyncIterator[None]:
    """FastAPI dependency that limits concurrent /ask executions.

    Must be a plain async generator (NOT @asynccontextmanager): FastAPI wraps
    the generator into a context manager itself. Decorating it again returns
    an _AsyncGeneratorContextManager that FastAPI then tries to use as an
    async iterator, which raises TypeError at dependency resolution time.
    """
    async with _ask_semaphore:
        yield

