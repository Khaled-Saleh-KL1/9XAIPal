"""Linkup web search client (https://linkup.so).

Agent-oriented search: real page content per result (not just a meta
description), plus a proper image-search mode via ``includeImages``. A single
endpoint returns a mixed list of text and image hits distinguished by
``type``, so both ``search`` and ``search_images`` call it and filter.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SEARCH_URL = "https://api.linkup.so/v1/search"
_TIMEOUT = 15.0


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.linkup_api_key}",
        "Content-Type": "application/json",
    }


async def _post(payload: dict) -> Optional[dict]:
    if not settings.linkup_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(_SEARCH_URL, json=payload, headers=_headers())
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Linkup search failed: {e}")
        return None


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Search Linkup and return normalized text results.

    ``categories`` is accepted and ignored — Linkup has no engine-group
    concept, same as Tavily. Kept only so the four clients stay drop-in
    swappable behind ``app.search.web``.
    """
    data = await _post({
        "q": query,
        "depth": "standard",
        "outputType": "searchResults",
    })
    if not data:
        return []

    results = []
    for item in data.get("results", []):
        if item.get("type") == "image":
            continue
        results.append({
            "title": item.get("name", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "source_engine": "linkup",
            "score": None,  # Linkup returns results pre-ranked, no numeric score.
        })
        if len(results) >= limit:
            break
    return results


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Image results via the same endpoint with ``includeImages: true``."""
    data = await _post({
        "q": query,
        "depth": "standard",
        "outputType": "searchResults",
        "includeImages": True,
    })
    if not data:
        return []

    out: list[dict] = []
    for item in data.get("results", []):
        if item.get("type") != "image":
            continue
        img_url = item.get("url") or ""
        if not img_url:
            continue
        out.append({
            "img_url": img_url,
            "thumbnail": img_url,
            "title": (item.get("name") or "").strip(),
            # Linkup's image results don't carry a separate hosting-page URL.
            "source_url": img_url,
            "source_engine": "linkup",
        })
        if len(out) >= limit:
            break
    return out


async def is_available() -> bool:
    """Whether Linkup is usable — key presence only, no network call.

    Same reasoning as tavily_client.is_available(): Linkup bills per search
    and exposes no free health endpoint, and this is polled by the
    container's own /health check every 15-30s forever.
    """
    return bool(settings.linkup_api_key)
