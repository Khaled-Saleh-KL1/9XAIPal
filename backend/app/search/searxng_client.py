"""SearXNG web search client."""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Search SearXNG and return normalized results."""
    url = f"{settings.searxng_url}/search"
    params = {
        "q": query,
        "format": "json",
        "pageno": 1,
    }
    if categories:
        params["categories"] = ",".join(categories)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.error(f"SearXNG search failed: {e}")
        return []

    results = []
    for item in data.get("results", [])[:limit]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "source_engine": item.get("engine", ""),
            "score": item.get("score"),
        })

    return results


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Search SearXNG image results and return ``img_url`` / ``thumbnail`` / ``title`` / ``source``.

    Falls back to an empty list on any failure (network, JSON error, no images
    category enabled). The caller (external_context) must tolerate ``[]`` and
    still produce normal text results.
    """
    url = f"{settings.searxng_url}/search"
    params = {
        "q": query,
        "format": "json",
        "categories": "images",
        "pageno": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning(f"SearXNG image search failed: {e}")
        return []

    out: list[dict] = []
    for item in data.get("results", [])[:limit]:
        img_url = item.get("img_src") or item.get("thumbnail_src") or item.get("url")
        if not img_url:
            continue
        # SearXNG returns //host/path or relative URLs for some engines — make absolute.
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        out.append({
            "img_url": img_url,
            "thumbnail": item.get("thumbnail_src") or img_url,
            "title": (item.get("title") or "").strip(),
            "source_url": item.get("url") or "",   # the page hosting the image
            "source_engine": item.get("engine", ""),
        })
    return out


async def is_available() -> bool:
    """Check if SearXNG is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.searxng_url}/healthz")
            return resp.status_code == 200
    except Exception:
        return False

