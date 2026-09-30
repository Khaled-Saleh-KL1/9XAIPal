"""Cached, bounded LLM re-ranking for Arabic retrieval results."""

import asyncio
import hashlib
import json

from sqlalchemy import text

from app.core import tracing
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.llm import client as llm_client

logger = get_logger(__name__)

_RERANK_TIMEOUT_SECONDS = 10
_RERANK_CACHE_TTL_SECONDS = 60 * 60
_RERANK_CACHE_PREFIX = "retrieval-rerank:"


def _is_enabled(is_arabic_query: bool, has_arabic_target: bool) -> bool:
    if is_arabic_query or has_arabic_target:
        return settings.rerank_arabic_enabled
    return settings.rerank_english_enabled


def _parse_order(content: str, candidate_count: int, minimum_count: int) -> list[int] | None:
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or len(value) < minimum_count:
        return None
    if any(not isinstance(number, int) or isinstance(number, bool) for number in value):
        return None
    if len(set(value)) != len(value) or any(number < 1 or number > candidate_count for number in value):
        return None
    return value


async def _candidate_headings(session, candidates: list[dict]) -> dict:
    missing_ids = [row["id"] for row in candidates if "heading_path" not in row and row.get("id")]
    if not missing_ids:
        return {}
    result = await session.execute(
        text("SELECT id, heading_path FROM chunks WHERE id = ANY(:ids)"),
        {"ids": missing_ids},
    )
    return {row["id"]: row["heading_path"] for row in result.mappings().all()}


def _messages(
    question: str, candidates: list[dict], headings: dict, minimum_count: int,
) -> list[dict]:
    rendered = []
    for index, row in enumerate(candidates, 1):
        heading = row.get("heading_path") or headings.get(row.get("id"), "") or ""
        snippet = (row.get("plain_text") or "")[:600]
        rendered.append(f"{index}. Heading: {heading}\n   Text: {snippet}")
    return [
        {
            "role": "system",
            "content": (
                "Re-rank retrieval candidates by how directly each passage answers the user's question. "
                "Return only a JSON list of candidate numbers in best-first order. You may drop clearly "
                "irrelevant candidates only if the list still contains at least the requested minimum count. "
                "Do not answer the question or add facts."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\nMinimum results to keep: {minimum_count}\n\n"
                "Candidates:\n" + "\n\n".join(rendered)
            ),
        },
    ]


async def _load_cached_order(cache_key: str, count: int, min_count: int) -> list[int] | None:
    try:
        value = await get_redis().get(cache_key)
        if not value:
            return None
        content = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        parsed = _parse_order(content, count, min_count)
        return parsed
    except Exception as exc:
        logger.debug("retrieval rerank cache read failed: %s", exc)
        return None


async def _rerank_and_cache(
    session, question: str, candidates: list[dict], cache_key: str, min_count: int,
) -> list[int] | None:
    headings = await _candidate_headings(session, candidates)
    messages = _messages(question, candidates, headings, min_count)
    response = await llm_client.chat(
        messages,
        role="chat",
        temperature=0.0,
        num_predict=max(64, len(candidates) * 8),
    )
    order = _parse_order(response.get("content") or "", len(candidates), min_count)
    if order is None:
        return None
    try:
        await get_redis().set(
            cache_key,
            json.dumps(order),
            ex=_RERANK_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.debug("retrieval rerank cache write failed: %s", exc)
    return order


async def _get_or_compute_order(
    session, question: str, pool: list[dict], cache_key: str, min_count: int,
) -> list[int] | None:
    order = await _load_cached_order(cache_key, len(pool), min_count)
    if order is not None:
        return order
    return await _rerank_and_cache(session, question, pool, cache_key, min_count)


@tracing.traced("retrieve.rerank", tracing.RETRIEVER, record_args=False)
async def rerank_chunks(
    session,
    question: str,
    candidates: list[dict],
    limit: int,
    *,
    is_arabic_query: bool,
    has_arabic_target: bool,
) -> list[dict]:
    """Return candidates in model order, or the fused order on any failure."""
    if not candidates or not _is_enabled(is_arabic_query, has_arabic_target):
        return candidates[:limit]

    pool_size = max(limit, settings.rerank_candidates)
    pool = candidates[:pool_size]
    candidate_ids = [str(row.get("id") or "") for row in pool]
    digest_input = question + "\0" + "\0".join(candidate_ids)
    digest = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()
    cache_key = f"{_RERANK_CACHE_PREFIX}{digest}"
    minimum_count = min(limit, len(pool))

    try:
        order = await asyncio.wait_for(
            _get_or_compute_order(session, question, pool, cache_key, minimum_count),
            timeout=_RERANK_TIMEOUT_SECONDS,
        )
        if order is None:
            return candidates[:limit]
        reranked = [pool[number - 1] for number in order]
        return reranked[:limit]
    except asyncio.TimeoutError:
        logger.warning("retrieval reranking exceeded its deadline")
    except Exception as exc:
        logger.warning("retrieval reranking failed: %s", exc)
    return candidates[:limit]
