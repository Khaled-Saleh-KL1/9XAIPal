"""DuckDuckGo web search — the ``ddgs`` library, not an HTTP API client.

Last in the cascade, by design: needs no API key and never runs out of
quota, so it's the one provider that's always configured — but it scrapes
an undocumented endpoint (there is no official DuckDuckGo search API), so
it's also the least reliable of the six. Every other provider gets tried
first; this is what's left standing if google, tavily, linkup, exa, and
serpapi have all failed or gone unconfigured.

``ddgs.DDGS`` is a synchronous, blocking client — every call here runs in a
worker thread via ``asyncio.to_thread`` so it doesn't stall the event loop
the way a raw blocking call inside an async function would.
"""

import asyncio
from typing import Optional

from ddgs import DDGS

from app.core.logging import get_logger
from app.search.errors import ProviderError

logger = get_logger(__name__)

_TIMEOUT = 15.0


def _text_sync(query: str, limit: int) -> list[dict]:
    with DDGS(timeout=_TIMEOUT) as ddgs:
        return list(ddgs.text(query, max_results=limit))


def _images_sync(query: str, limit: int) -> list[dict]:
    with DDGS(timeout=_TIMEOUT) as ddgs:
        return list(ddgs.images(query, max_results=limit))


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Search DuckDuckGo and return normalized results.

    ``categories`` is accepted and ignored, same as every other client.
    """
    try:
        raw = await asyncio.to_thread(_text_sync, query, limit)
    except Exception as e:
        raise ProviderError(f"DuckDuckGo search failed: {e}") from e

    results = []
    for item in raw[:limit]:
        results.append({
            "title": item.get("title") or "",
            "url": item.get("href") or "",
            "snippet": item.get("body") or "",
            "source_engine": "duckduckgo",
            "score": None,  # ddgs returns results in its own rank order already.
        })
    return results


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Image results via ``ddgs.images``."""
    try:
        raw = await asyncio.to_thread(_images_sync, query, limit)
    except Exception as e:
        raise ProviderError(f"DuckDuckGo image search failed: {e}") from e

    out: list[dict] = []
    for item in raw[:limit]:
        img_url = item.get("image") or ""
        if not img_url:
            continue
        out.append({
            "img_url": img_url,
            "thumbnail": item.get("thumbnail") or img_url,
            "title": (item.get("title") or "").strip(),
            "source_url": item.get("url") or img_url,  # the page hosting the image
            "source_engine": "duckduckgo",
        })
    return out


async def is_available() -> bool:
    """Always True — no key, no billing, nothing to be unconfigured about.

    Unlike every other provider this genuinely could do a live probe cheaply
    (no quota to protect), but /health polls this every 15-30s forever and a
    real network round-trip on that cadence buys nothing a request-time
    failure (already handled: search() degrades to []) doesn't already cover.
    """
    return True


def is_configured() -> bool:
    """Always True — see is_available(). No network call, matches every
    other client's is_configured contract (though nothing here can be
    "unconfigured" the way a missing API key would be)."""
    return True
