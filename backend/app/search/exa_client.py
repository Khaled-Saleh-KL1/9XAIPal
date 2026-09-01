"""Exa web search client (https://exa.ai).

Neural/semantic search over an indexed corpus — strong on academic and
technical sources (verified: for "FlashAttention 2 paper" it returned the
real arXiv paper as result #1, unprompted). Last in the cascade: smallest
free quota of the four, and no image-search endpoint at all — it indexes
documents, not a SERP, so ``search_images`` always returns ``[]``.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.search.errors import ProviderError

logger = get_logger(__name__)

_SEARCH_URL = "https://api.exa.ai/search"
_TIMEOUT = 15.0


def _headers() -> dict:
    return {
        "x-api-key": settings.exa_api_key,
        "Content-Type": "application/json",
    }


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Search Exa and return normalized results.

    ``categories`` is accepted and ignored, same as tavily_client/
    linkup_client — Exa has no engine-group concept.

    ``contents.text.maxCharacters`` is capped at the same 500 chars
    ``rank_results`` trims snippets to anyway, so there's no reason to pay
    for (or wait on) more text per result than the app will ever show.
    """
    if not settings.exa_api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                _SEARCH_URL,
                headers=_headers(),
                json={
                    "query": query,
                    "numResults": max(1, min(limit, 25)),
                    "contents": {"text": {"maxCharacters": 500}},
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        raise ProviderError(f"Exa search failed: {e}") from e

    results = []
    for item in data.get("results", [])[:limit]:
        results.append({
            "title": item.get("title") or "",
            "url": item.get("url", ""),
            "snippet": item.get("text") or "",
            "source_engine": "exa",
            "score": item.get("score"),  # present on some result types, absent on others.
        })
    return results


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Exa has no image-search endpoint — always empty.

    It's a document index, not a SERP: results are pages, not media. The
    caller (external_context / agent_tools) already tolerates an empty list
    from any provider and still produces a normal text answer.
    """
    return []


async def is_available() -> bool:
    """Whether Exa is usable — key presence only, no network call.

    Same reasoning as tavily_client.is_available(): billed per search, no
    free health endpoint, and this is polled by /health every 15-30s forever.
    """
    return bool(settings.exa_api_key)
