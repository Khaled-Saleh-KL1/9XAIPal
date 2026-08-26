"""FastAPI dependencies."""

import os
from typing import AsyncIterator
from uuid import UUID

from fastapi import Depends, HTTPException, Request
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
# Auth: session cookie -> current user.
# ------------------------------------------------------------------


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    """The logged-in user, or 401 if the session cookie is missing/invalid/expired.

    A cross-tenant resource access (a valid session hitting someone else's
    paper/study/etc.) is NOT this dependency's job — that 404s at the point
    the resource is loaded (see the endpoints' `_require_document`-style
    helpers), not here. This only establishes "who is asking".
    """
    from app.core.auth import get_session_user_id
    from app.database.repositories import users as user_repo

    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user_id = await get_session_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired — please log in again")

    user = await user_repo.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired — please log in again")

    return user


async def get_current_user_optional(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict | None:
    """Like get_current_user, but returns None instead of 401 — for GET /auth/me,
    which the frontend polls on load precisely to find out whether anyone is
    logged in."""
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


# ------------------------------------------------------------------
# Rate limiting for the auth endpoints specifically. The app-wide
# RateLimitMiddleware (app/core/security.py) is deliberately generic and, per
# its own docstring, isn't even correctly shared across the API's
# --workers 2 processes — nowhere near tight enough to blunt credential
# stuffing or invite-code brute-forcing. This is a separate, stricter,
# Redis-backed limiter (Redis is already required for sessions), so it works
# correctly regardless of worker count.
# ------------------------------------------------------------------

_AUTH_RATE_LIMIT = 10  # attempts
_AUTH_RATE_WINDOW_SECONDS = 60


async def enforce_auth_rate_limit(request: Request) -> None:
    from app.core.redis import get_redis

    ip = request.client.host if request.client else "unknown"
    key = f"authrl:{ip}"
    r = get_redis()
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, _AUTH_RATE_WINDOW_SECONDS)
    if count > _AUTH_RATE_LIMIT:
        ttl = await r.ttl(key)
        raise HTTPException(
            status_code=429,
            detail="Too many attempts — slow down and retry shortly.",
            headers={"Retry-After": str(max(1, ttl))},
        )


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


def get_ask_semaphore() -> asyncio.Semaphore:
    """The shared /ask concurrency limiter, for callers that must hold it
    beyond the Depends lifecycle (the SSE streaming endpoint acquires it
    inside its response generator, which runs after dependencies close)."""
    return _ask_semaphore

