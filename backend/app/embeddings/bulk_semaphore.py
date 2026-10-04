"""Redis-backed leases limiting concurrent synchronous bulk embedding calls."""

from contextlib import contextmanager
from functools import lru_cache
import threading
import time
from typing import Iterator
from uuid import uuid4

import redis

from app.core.config import settings
from app.core.logging import get_logger


_DEFAULT_KEY_PREFIX = "9xaipal:bulk-embedding"
_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
logger = get_logger(__name__)


class BulkEmbeddingPermitError(RuntimeError):
    """Bulk work could not safely acquire or retain a Redis permit."""


@lru_cache(maxsize=8)
def _local_fallback_semaphore(limit: int) -> threading.BoundedSemaphore:
    """Bound concurrent bulk calls within this process while Redis is down."""
    return threading.BoundedSemaphore(limit)


@lru_cache(maxsize=4)
def _redis_client_for_url(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )


def _acquire_slot(
    client: redis.Redis,
    key_prefix: str,
    max_inflight: int,
    ttl_ms: int,
    poll_interval_s: float,
) -> tuple[str, str]:
    while True:
        for slot in range(max_inflight):
            key = f"{key_prefix}:slot:{slot}"
            token = uuid4().hex
            try:
                if client.set(key, token, nx=True, px=ttl_ms):
                    return key, token
            except Exception as exc:
                raise BulkEmbeddingPermitError(
                    "Redis unavailable; refusing bulk embedding work"
                ) from exc
        time.sleep(poll_interval_s)


@contextmanager
def bulk_embedding_permit(
    *,
    redis_client: redis.Redis | None = None,
    key_prefix: str | None = None,
    max_inflight: int | None = None,
    ttl_s: float | None = None,
    poll_interval_s: float = 0.1,
) -> Iterator[None]:
    """Hold one cross-process permit until the protected bulk request ends.

    The key's random token allows renewal and release only by its owner. A
    daemon heartbeat extends the lease while this process remains alive; a
    hard crash leaves the finite TTL to make the slot available again.
    ``redis_client`` and ``key_prefix`` are injectable for isolated tests.
    """
    limit = settings.bulk_embedding_max_inflight if max_inflight is None else max_inflight
    ttl = settings.bulk_embedding_semaphore_ttl_s if ttl_s is None else ttl_s
    prefix = _DEFAULT_KEY_PREFIX if key_prefix is None else key_prefix
    if limit <= 0:
        raise ValueError("max_inflight must be positive")
    if ttl <= 0:
        raise ValueError("ttl_s must be positive")
    if poll_interval_s <= 0:
        raise ValueError("poll_interval_s must be positive")

    try:
        client = redis_client or _redis_client_for_url(settings.redis_url)
        key, token = _acquire_slot(
            client, prefix, limit, max(1, int(ttl * 1000)), poll_interval_s
        )
    except Exception as exc:
        logger.warning(
            "Redis bulk-embedding permit unavailable; using a local limit of %d "
            "for this process",
            limit,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        with _local_fallback_semaphore(limit):
            yield
        return

    ttl_ms = max(1, int(ttl * 1000))
    stop_heartbeat = threading.Event()
    heartbeat_errors: list[Exception] = []

    def renew_lease() -> None:
        interval = max(0.01, ttl / 3)
        while not stop_heartbeat.wait(interval):
            try:
                renewed = client.eval(_RENEW_SCRIPT, 1, key, token, ttl_ms)
            except Exception as exc:
                heartbeat_errors.append(exc)
                return
            if not renewed:
                heartbeat_errors.append(
                    BulkEmbeddingPermitError("bulk embedding lease was lost")
                )
                return

    heartbeat = threading.Thread(
        target=renew_lease, name="bulk-embedding-lease", daemon=True
    )
    heartbeat.start()
    try:
        yield
    finally:
        stop_heartbeat.set()
        heartbeat.join()
        release_error: Exception | None = None
        released = False
        try:
            released = bool(client.eval(_RELEASE_SCRIPT, 1, key, token))
        except Exception as exc:
            release_error = exc

        if heartbeat_errors:
            logger.warning(
                "Redis bulk-embedding lease renewal failed; allowing the current "
                "request to finish and relying on release or TTL expiry",
                exc_info=(
                    type(heartbeat_errors[0]), heartbeat_errors[0],
                    heartbeat_errors[0].__traceback__,
                ),
            )
        if release_error is not None:
            logger.warning(
                "Redis bulk-embedding lease release failed; its finite TTL will "
                "expire the permit",
                exc_info=(type(release_error), release_error, release_error.__traceback__),
            )
        elif not released:
            logger.warning(
                "Redis bulk-embedding lease was no longer owned at release; "
                "its former key will expire by TTL"
            )
