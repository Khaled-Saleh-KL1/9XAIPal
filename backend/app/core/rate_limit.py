"""A shared minimum-interval gate, coordinated through Redis.

Unlike :mod:`app.core.circuit_breaker` next door — which is deliberately
per-process because it only optimizes latency — this one has to be shared to
be correct at all. A provider's "40 requests per minute" is counted at the
provider, across every process that holds the key, so a per-process gate
simply multiplies the real rate by the number of processes:

    uvicorn --workers 2  +  celery worker  =  3 processes
    3 x 40 RPM on one key  ->  120 RPM against a 40 RPM limit  ->  429s

So the "when may I go next" timestamp lives in Redis, one entry per key, and
callers reserve their slot atomically. The reservation returns how long the
caller must wait, and the caller sleeps that long before its request; slots
are handed out in arrival order, so concurrent callers queue instead of
stampeding.

⚠ Time comes from ``redis.call('TIME')``, not from the caller. The whole
point is a single shared clock: two containers with a few hundred ms of
drift would otherwise each believe they were inside their own budget.

If Redis is unreachable the gate degrades to a per-process one rather than
failing the request — a possible 429 beats a guaranteed outage.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

import redis as redis_sync

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

# Reserve the next slot for `interval` seconds from whenever the last one was
# taken (or from now, if the gate has been idle). Returns the caller's wait in
# seconds, as a string because Redis truncates Lua numbers to integers.
#
# The key expires on its own once the queue it describes has drained, so an
# unused provider leaves nothing behind.
_RESERVE_LUA = """
local interval = tonumber(ARGV[1])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local next_free = tonumber(redis.call('GET', KEYS[1]) or '0')
if next_free < now then
  next_free = now
end
local wait = next_free - now
redis.call('SET', KEYS[1], next_free + interval, 'PX', math.ceil((wait + interval) * 1000) + 5000)
return tostring(wait)
"""

# A wait longer than this means far more demand than the key's budget can
# serve. It is still honored — waiting beats a 429 — but it is worth a log
# line, because the real fix is another key or fewer calls, not more patience.
_SLOW_WAIT_WARN_SECONDS = 10.0

# Per-process fallback, used only when Redis is unreachable.
_local_next_free: dict[str, float] = {}
_local_async_locks: dict[str, asyncio.Lock] = {}
_local_sync_locks: dict[str, threading.Lock] = {}
_local_sync_guard = threading.Lock()

_sync_client: Optional["redis_sync.Redis"] = None


def _redis_key(name: str) -> str:
    return f"ratelimit:next_free:{name}"


def _get_sync_redis() -> "redis_sync.Redis":
    """Sync Redis client for Celery workers (app.core.redis is async-only)."""
    global _sync_client
    if _sync_client is None:
        _sync_client = redis_sync.from_url(settings.redis_url, decode_responses=True)
    return _sync_client


def _local_reserve(name: str, interval: float) -> float:
    """Reserve against this process's own clock. Callers already hold the
    matching lock, so the read-modify-write below cannot interleave."""
    now = time.monotonic()
    next_free = max(_local_next_free.get(name, 0.0), now)
    _local_next_free[name] = next_free + interval
    return next_free - now


def _warn_if_slow(name: str, wait: float) -> None:
    if wait > _SLOW_WAIT_WARN_SECONDS:
        logger.warning(
            "rate limit queue for %s is %.1fs deep — demand exceeds this key's budget",
            name, wait,
        )


async def acquire(name: str, interval_seconds: float) -> None:
    """Wait until this process may issue the next call for ``name``.

    ``name`` identifies the budget, not the caller: every process using the
    same provider key must pass the same name for the gate to mean anything.
    """
    try:
        raw = await get_redis().eval(_RESERVE_LUA, 1, _redis_key(name), interval_seconds)
        wait = float(raw)
    except Exception as e:
        logger.warning("rate limit: Redis unavailable (%s); falling back to per-process gate", e)
        async with _local_async_lock(name):
            wait = _local_reserve(name, interval_seconds)
            if wait > 0:
                await asyncio.sleep(wait)
        return

    _warn_if_slow(name, wait)
    if wait > 0:
        await asyncio.sleep(wait)


def acquire_sync(name: str, interval_seconds: float) -> None:
    """Sync variant, for Celery workers. Same budget, same Redis entry."""
    try:
        raw = _get_sync_redis().eval(_RESERVE_LUA, 1, _redis_key(name), interval_seconds)
        wait = float(raw)
    except Exception as e:
        logger.warning("rate limit: Redis unavailable (%s); falling back to per-process gate", e)
        with _local_sync_lock(name):
            wait = _local_reserve(name, interval_seconds)
            if wait > 0:
                time.sleep(wait)
        return

    _warn_if_slow(name, wait)
    if wait > 0:
        time.sleep(wait)


def _local_async_lock(name: str) -> asyncio.Lock:
    # No guard lock needed: asyncio is cooperative and nothing here awaits
    # between the get and the set, so no other coroutine can interleave.
    lock = _local_async_locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _local_async_locks[name] = lock
    return lock


def _local_sync_lock(name: str) -> threading.Lock:
    with _local_sync_guard:
        lock = _local_sync_locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _local_sync_locks[name] = lock
        return lock


def reset() -> None:
    """Drop per-process fallback state (tests, reconfigure)."""
    _local_next_free.clear()
    _local_async_locks.clear()
    _local_sync_locks.clear()
