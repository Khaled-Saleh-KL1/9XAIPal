"""SerpApi web search client (https://serpapi.com).

A paid intermediary that scrapes real Google (and other engines') result
pages and returns them as structured JSON — genuine Google SERP data,
unlike google_client.py's Search-grounding approach (Gemini's own
summarized-and-grounded answer). Fifth in the cascade, ahead of only the
keyless DuckDuckGo fallback.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_URL = "https://serpapi.com/search.json"
_TIMEOUT = 20.0


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Search Google via SerpApi and return normalized results.

    ``categories`` is accepted and ignored, same as every other client.
    """
    if not settings.serpapi_api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(_URL, params={
                "engine": "google",
                "q": query,
                "api_key": settings.serpapi_api_key,
            })
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.error(f"SerpApi search failed: {e}")
        return []

    if data.get("error"):
        logger.error(f"SerpApi search failed: {data['error']}")
        return []

    results = []
    for item in data.get("organic_results", [])[:limit]:
        results.append({
            "title": item.get("title") or "",
            "url": item.get("link") or "",
            "snippet": item.get("snippet") or "",
            "source_engine": "serpapi",
            "score": None,  # organic_results is already Google's own rank order.
        })
    return results


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Image results via SerpApi's ``google_images`` engine."""
    if not settings.serpapi_api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(_URL, params={
                "engine": "google_images",
                "q": query,
                "api_key": settings.serpapi_api_key,
            })
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning(f"SerpApi image search failed: {e}")
        return []

    if data.get("error"):
        logger.warning(f"SerpApi image search failed: {data['error']}")
        return []

    out: list[dict] = []
    for item in data.get("images_results", [])[:limit]:
        img_url = item.get("original") or item.get("thumbnail") or ""
        if not img_url:
            continue
        out.append({
            "img_url": img_url,
            "thumbnail": item.get("thumbnail") or img_url,
            "title": (item.get("title") or "").strip(),
            "source_url": item.get("link") or img_url,  # the page hosting the image
            "source_engine": "serpapi",
        })
    return out


async def is_available() -> bool:
    """Whether SerpApi is usable — key presence only, no network call.

    Same reasoning as every other provider: billed/quota-limited, no free
    health endpoint, and this is polled by /health every 15-30s forever.
    """
    return bool(settings.serpapi_api_key)
