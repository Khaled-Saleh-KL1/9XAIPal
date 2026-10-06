"""Short-lived, shared availability state learned from real chat requests.

Unknown and cached-positive models are treated as available. Only provider
responses that identify a billing, authentication, or missing-model problem
are cached as unavailable; temporary errors do not hide a model in the picker.
"""

import asyncio
import hashlib
import json
from typing import Iterable

import redis

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

AVAILABILITY_TTL_SECONDS = 6 * 60 * 60
_REDIS_READ_TIMEOUT_SECONDS = 1.0
_UNAVAILABLE_STATUS_CODES = {401, 403, 402, 404}
_sync_redis: redis.Redis | None = None


def unavailable_reason(provider: str, status_code: int | None) -> str | None:
    if status_code == 402:
        return (
            "needs a paid Ollama plan"
            if provider == "ollama"
            else f"needs a paid {provider.upper()} plan"
        )
    if status_code in (401, 403):
        return "provider authentication failed"
    if status_code == 404:
        return "model not found"
    return None


def _key(provider: str, model: str) -> str:
    model_hash = hashlib.sha256(model.encode("utf-8")).hexdigest()
    return f"llm:availability:v1:{provider}:{model_hash}"


def _default_state() -> dict:
    return {"available": True, "unavailable_reason": None}


async def record_model_result(
    provider: str,
    model: str,
    *,
    available: bool,
    status_code: int | None = None,
) -> None:
    """Cache one real model response, degrading silently when Redis is down."""
    reason = None if available else unavailable_reason(provider, status_code)
    if not model or (not available and reason is None):
        return
    value = {"available": bool(available)}
    if reason is not None:
        value["unavailable_reason"] = reason
    try:
        await get_redis().set(
            _key(provider, model), json.dumps(value, separators=(",", ":")),
            ex=AVAILABILITY_TTL_SECONDS,
        )
    except Exception:
        logger.debug("model availability write skipped because Redis is unavailable")


async def get_model_availability(
    models: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], dict]:
    """Bulk-read cached availability; unknown, malformed, or failed reads are available."""
    pairs = list(dict.fromkeys(models))
    states = {pair: _default_state() for pair in pairs}
    if not pairs:
        return states
    keys = [_key(provider, model) for provider, model in pairs]
    try:
        values = await asyncio.wait_for(
            get_redis().mget(keys), timeout=_REDIS_READ_TIMEOUT_SECONDS
        )
    except Exception:
        logger.debug("model availability read skipped because Redis is unavailable")
        return states
    for pair, raw in zip(pairs, values):
        if raw is None:
            continue
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(decoded, dict) or not isinstance(decoded.get("available"), bool):
            continue
        states[pair] = {
            "available": decoded["available"],
            "unavailable_reason": (
                str(decoded.get("unavailable_reason") or "model unavailable")
                if decoded["available"] is False
                else None
            ),
        }
    return states


def _get_sync_redis() -> redis.Redis:
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
    return _sync_redis


def record_model_result_sync(
    provider: str,
    model: str,
    *,
    available: bool,
    status_code: int | None = None,
) -> None:
    """Synchronous counterpart used by Celery chat calls."""
    reason = None if available else unavailable_reason(provider, status_code)
    if not model or (not available and reason is None):
        return
    value = {"available": bool(available)}
    if reason is not None:
        value["unavailable_reason"] = reason
    try:
        _get_sync_redis().set(
            _key(provider, model), json.dumps(value, separators=(",", ":")),
            ex=AVAILABILITY_TTL_SECONDS,
        )
    except Exception:
        logger.debug("model availability write skipped because Redis is unavailable")
