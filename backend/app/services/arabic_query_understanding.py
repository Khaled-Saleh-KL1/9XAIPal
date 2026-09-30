"""Cached Arabic question rewrites used to expand retrieval queries."""

import asyncio
import hashlib
import json

from app.core import tracing
from app.core.language import is_primarily_arabic
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.llm import client as llm_client

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 24 * 60 * 60
_UNDERSTANDING_TIMEOUT_SECONDS = 8
_CACHE_PREFIX = "arabic-query-understanding:"


def _parse_understanding(content: str) -> dict | None:
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None

    msa = value.get("msa")
    english = value.get("english")
    keywords = value.get("keywords")
    if not isinstance(msa, str) or not msa.strip():
        return None
    if not is_primarily_arabic(msa):
        return None
    if not isinstance(english, str) or not english.strip():
        return None
    if not isinstance(keywords, list) or not 3 <= len(keywords) <= 8:
        return None
    cleaned_keywords = [word.strip() for word in keywords if isinstance(word, str)]
    if len(cleaned_keywords) != len(keywords) or any(not word for word in cleaned_keywords):
        return None
    if any(not is_primarily_arabic(word) for word in cleaned_keywords):
        return None
    return {
        "msa": msa.strip(),
        "keywords": cleaned_keywords,
        "english": english.strip(),
    }


async def _understand_and_cache(question: str, cache_key: str) -> dict | None:
    try:
        cached = await get_redis().get(cache_key)
        if cached:
            cached_text = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
            parsed = _parse_understanding(cached_text)
            if parsed is not None:
                return parsed
    except Exception as exc:
        logger.debug("Arabic query understanding cache read failed: %s", exc)

    messages = [
        {
            "role": "system",
            "content": (
                "Rewrite the user's Arabic search question for retrieval only. "
                "Preserve the user's intent exactly; do not answer the question; "
                "do not add facts. Return only one valid JSON object with exactly "
                "these keys: msa, keywords, english. The msa value must be a "
                "formal Modern Standard Arabic rewrite, converting dialect to "
                "MSA and correcting spelling. The keywords value must be an "
                "array of 3 to 8 short Arabic search terms, including useful "
                "synonyms and singular/plural forms. The english value must be "
                "a faithful English translation and must keep technical terms."
            ),
        },
        {"role": "user", "content": question},
    ]
    response = await llm_client.chat(
        messages,
        role="chat",
        temperature=0.0,
        num_predict=384,
    )
    result = _parse_understanding(response.get("content") or "")
    if result is None:
        return None
    try:
        await get_redis().set(
            cache_key,
            json.dumps(result, ensure_ascii=False),
            ex=_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.debug("Arabic query understanding cache write failed: %s", exc)
    return result


@tracing.traced(
    "retrieve.arabic_query_understanding", tracing.RETRIEVER, record_args=False
)
async def understand_arabic_query(question: str) -> dict | None:
    """Return MSA, Arabic keyword, and English search variants within 8 seconds."""
    if not question.strip():
        return None
    digest = hashlib.sha1(question.encode("utf-8")).hexdigest()
    cache_key = f"{_CACHE_PREFIX}{digest}"
    try:
        return await asyncio.wait_for(
            _understand_and_cache(question, cache_key),
            timeout=_UNDERSTANDING_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Arabic query understanding exceeded its deadline")
        return None
    except Exception as exc:
        logger.warning("Arabic query understanding failed: %s", exc)
        return None
