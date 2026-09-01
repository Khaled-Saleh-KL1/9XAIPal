"""Google web search client — Custom Search JSON API.

⚠ This replaced an earlier attempt that used Gemini's Search-grounding tool
(a `generateContent` call with `tools: [{"google_search": {}}]`). That was a
dead end here: the key available for it was free-tier, and free-tier Gemini
is unavailable in the EEA, so every grounding call from this EU-hosted
server returned 429 RESOURCE_EXHAUSTED — 100% failure, forever, with no
code-side fix. Custom Search is the right surface anyway: it returns an
actual SERP (not a model's grounded prose), and it has a real image mode,
which grounding never had.

**Setup is three things, and two of them are outside this repo:**

1. ``GOOGLE_API_KEY`` — a Google Cloud API key (the ``AIzaSy...`` form).
2. The **Custom Search API must be enabled** on that key's Cloud project,
   or every call returns ``403 PERMISSION_DENIED: This project does not
   have the access to Custom Search JSON API``.
3. ``GOOGLE_SEARCH_CX`` — a Programmable Search Engine ID from
   <https://programmablesearchengine.google.com/>, configured to search the
   entire web. Without it the API returns ``400 INVALID_ARGUMENT``; there
   is no "just search Google" mode.

⚠ **This provider is metered and this module refuses to exceed the free
tier.** Custom Search allows 100 queries/day free and then bills. Every
call reserves a slot through :mod:`app.search.quota` first, and is skipped
entirely once ``GOOGLE_SEARCH_DAILY_LIMIT`` (default 100) is used up for the
day, or if the counter can't be read at all. Text and image searches draw
on the SAME quota — Google counts them together, so this does too.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.search.errors import ProviderError
from app.search import quota

logger = get_logger(__name__)

_URL = "https://www.googleapis.com/customsearch/v1"
_TIMEOUT = 15.0

# Custom Search caps `num` at 10 per request and bills per request, so there
# is never a reason to ask for more than one page.
_MAX_PER_REQUEST = 10

_QUOTA_PROVIDER = "google"


def _configured() -> bool:
    return bool(settings.google_api_key and settings.google_search_cx)


async def _get(params: dict, *, label: str) -> Optional[dict]:
    """One Custom Search call, quota-guarded.

    Returns None when the provider is unconfigured or its daily free quota
    is spent — both are "skip me", not failures, so they must not count
    against the circuit breaker (a used-up quota is the provider working
    exactly as intended).
    """
    if not _configured():
        return None
    if not await quota.try_consume(_QUOTA_PROVIDER, settings.google_search_daily_limit):
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(_URL, params={
                "key": settings.google_api_key,
                "cx": settings.google_search_cx,
                **params,
            })
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:300]
        raise ProviderError(
            f"{label} failed: HTTP {e.response.status_code} {body}"
        ) from e
    except Exception as e:
        raise ProviderError(f"{label} failed: {e}") from e


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Search Google and return normalized results.

    ``categories`` is accepted and ignored, same as every other client.
    """
    data = await _get(
        {"q": query, "num": max(1, min(limit, _MAX_PER_REQUEST))},
        label="Google search",
    )
    if not data:
        return []

    results = []
    for item in data.get("items", [])[:limit]:
        results.append({
            "title": item.get("title") or "",
            "url": item.get("link") or "",
            "snippet": item.get("snippet") or "",
            "source_engine": "google",
            # items come back in Google's own rank order; there is no
            # per-result score to compare against other providers'.
            "score": None,
        })
    return results


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Image results via Custom Search's ``searchType=image`` mode.

    ⚠ Draws on the same 100/day free quota as text search — Google counts
    both against one limit.
    """
    data = await _get(
        {
            "q": query,
            "searchType": "image",
            "num": max(1, min(limit, _MAX_PER_REQUEST)),
        },
        label="Google image search",
    )
    if not data:
        return []

    out: list[dict] = []
    for item in data.get("items", [])[:limit]:
        img_url = item.get("link") or ""
        if not img_url:
            continue
        image = item.get("image") or {}
        out.append({
            "img_url": img_url,
            "thumbnail": image.get("thumbnailLink") or img_url,
            "title": (item.get("title") or "").strip(),
            # The page the image sits on, not the image file itself.
            "source_url": image.get("contextLink") or img_url,
            "source_engine": "google",
        })
    return out


async def is_available() -> bool:
    """Whether Google is usable — configuration only, no network call.

    Deliberately does not check remaining quota: /health polls this every
    15-30s, and a Redis round-trip per poll to answer a question nobody
    asked is the same waste the other providers' is_available() avoids. A
    spent quota surfaces as the provider being skipped at search time.
    """
    return _configured()
