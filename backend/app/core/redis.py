"""Async Redis client, shared by session storage (see app.core.auth).

Redis is already a hard dependency (Celery broker/backend), but nothing
async touches it anywhere else in this codebase — Celery owns its own
connection internally. This is a separate, small client for the request path.
"""

from typing import Optional

import redis.asyncio as redis

from app.core.config import settings

_client: Optional["redis.Redis"] = None


def get_redis() -> "redis.Redis":
    """Return the shared async Redis client, creating it on first use."""
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    """Close the shared client. Called from lifespan shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
