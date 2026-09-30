"""Short-lived, cached translations used to expand retrieval queries."""

import asyncio
import hashlib

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.llm import client as llm_client

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 24 * 60 * 60
_TRANSLATION_TIMEOUT_SECONDS = 8
_TARGET_NAMES = {"arabic": "Arabic", "english": "English"}


async def translated_query(query: str, target_language: str) -> str | None:
    """Translate a retrieval query, returning ``None`` when unavailable.

    Only successful, non-empty translations are cached. Redis is best-effort:
    a cache outage must not prevent the existing chat model from translating.
    """
    target = target_language.strip().lower()
    target_name = _TARGET_NAMES.get(target)
    if not query.strip() or target_name is None:
        return None

    digest = hashlib.sha1(query.encode("utf-8")).hexdigest()
    cache_key = f"query-translation:{digest}:{target}"
    try:
        cached = await get_redis().get(cache_key)
        if cached and str(cached).strip():
            return str(cached).strip()
    except Exception as exc:
        logger.debug("query translation cache read failed: %s", exc)

    messages = [
        {
            "role": "system",
            "content": (
                f"Translate the search query faithfully to {target_name}. "
                "Keep technical terms, symbols, and formulas unchanged. "
                "Output only the translation."
            ),
        },
        {"role": "user", "content": query},
    ]
    try:
        response = await asyncio.wait_for(
            llm_client.chat(
                messages,
                role="chat",
                temperature=0.0,
                num_predict=128,
            ),
            timeout=_TRANSLATION_TIMEOUT_SECONDS,
        )
        translation = str(response.get("content") or "").strip()
    except Exception as exc:
        logger.warning("query translation to %s failed: %s", target, exc)
        return None

    if not translation:
        return None
    try:
        await get_redis().set(cache_key, translation, ex=_CACHE_TTL_SECONDS)
    except Exception as exc:
        logger.debug("query translation cache write failed: %s", exc)
    return translation
