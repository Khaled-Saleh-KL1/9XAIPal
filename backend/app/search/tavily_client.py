"""Tavily web search client.

Tavily is a search API built for LLM agents: one HTTPS call returns ranked,
already-summarised page extracts rather than a SERP that still has to be
scraped. Second in the cascade (see app/search/web.py), after Google.

⚠ **This provider is a network egress.** The query string leaves for
``api.tavily.com``. Paper text, chunks, and chat history never go out —
only the query.

The module deliberately mirrors the other three clients' three functions and
their return shapes, so all four are interchangeable behind ``app.search.web``.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SEARCH_URL = "https://api.tavily.com/search"

# Tavily bills per call and its own timeout is generous; an "advanced" depth
# search regularly takes 5-8s. 20s leaves room for that without letting a
# hung connection hold an /ask request open indefinitely.
_TIMEOUT = 20.0


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.tavily_api_key}",
        "Content-Type": "application/json",
    }


async def _post(payload: dict) -> Optional[dict]:
    """One Tavily call. Returns None on any failure — callers degrade to []."""
    if not settings.tavily_api_key:
        logger.warning("Tavily search requested but TAVILY_API_KEY is empty")
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(_SEARCH_URL, json=payload, headers=_headers())
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        # 401 = bad key, 429 = quota. Both are operator problems, not transient,
        # so they are worth naming rather than folding into a generic warning.
        body = (e.response.text or "")[:200]
        logger.error("Tavily search failed: HTTP %s %s", e.response.status_code, body)
        return None
    except Exception as e:
        logger.error("Tavily search failed: %s", e)
        return None


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Search the web and return normalized results.

    ``categories`` is accepted and ignored, same as every other client —
    none of the four providers have an engine-group concept. The domain bias
    that idea used to buy lives in
    :func:`app.chat.external_context.rewrite_query_for_papers` instead, which
    works for all of them.
    """
    data = await _post({
        "query": query,
        "max_results": max(1, min(limit, 20)),
        "search_depth": settings.tavily_search_depth,
        "include_answer": False,
        "include_raw_content": False,
    })
    if not data:
        return []

    results = []
    for item in data.get("results", [])[:limit]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            # Tavily calls the extract "content"; the rest of the app calls it
            # a snippet, and rank_results trims it to 500 chars.
            "snippet": item.get("content", ""),
            "source_engine": "tavily",
            "score": item.get("score"),
        })
    return results


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Image results, in the same shape every other client's ``search_images`` returns.

    Tavily has no separate image endpoint: images ride along on a normal search
    when ``include_images`` is set. That makes this a second billed call for the
    same query — acceptable because the caller only asks for images when the
    question looks visual, and cheaper than the alternative of always requesting
    them and discarding the result.

    ⚠ An entry may be a bare URL string on older API versions and an object
    (``{url, title, description}``) on the current one. Both are handled: a
    provider that changes shape must not turn every figure into a crash.
    """
    data = await _post({
        "query": query,
        # Text results are not wanted here, but 0 is rejected — ask for the
        # minimum and drop them.
        "max_results": 1,
        "search_depth": "basic",
        "include_images": True,
        "include_image_descriptions": True,
        "include_answer": False,
    })
    if not data:
        return []

    out: list[dict] = []
    for item in data.get("images", [])[:limit]:
        if isinstance(item, str):
            url, title = item, ""
        else:
            url = item.get("url") or ""
            title = (item.get("title") or item.get("description") or "").strip()
        if not url:
            continue
        out.append({
            "img_url": url,
            # Tavily serves no thumbnails. Pointing both at the full image keeps
            # the consumer contract intact; the images are web-sized already.
            "thumbnail": url,
            "title": title,
            "source_url": url,
            "source_engine": "tavily",
        })
    return out


async def is_available() -> bool:
    """Whether Tavily is usable — i.e. whether a key is configured.

    ⚠ **This deliberately does not touch the network.** Tavily exposes no
    health endpoint, so the only way to verify a key is to spend a search
    credit on it — and `/api/v1/health` is the container's healthcheck, polled
    every 30 seconds forever. A live probe here would quietly bill ~2,900
    searches a day to answer a question nobody asked. A bad key surfaces as a
    logged 401 on the first real search instead.
    """
    return bool(settings.tavily_api_key)
