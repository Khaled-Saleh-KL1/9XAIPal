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

from app.core import circuit_breaker
from app.core.config import settings
from app.core.logging import get_logger
from app.search.errors import ProviderError

logger = get_logger(__name__)

_SEARCH_URL = "https://api.tavily.com/search"

# Tavily bills per call and its own timeout is generous; an "advanced" depth
# search regularly takes 5-8s. 20s leaves room for that without letting a
# hung connection hold an /ask request open indefinitely.
_TIMEOUT = 20.0


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _breaker_id(index: int) -> str:
    """Circuit-breaker name for one key. Per-KEY, not per-provider: an
    exhausted key must stop being tried without taking Tavily as a whole
    out of the cascade, since the other keys are still good."""
    return f"tavily#{index}"


# Statuses that mean "this KEY is done" rather than "Tavily is down".
# Rotating to the next key only helps for these; for a 500 or a network
# error every key would hit the same wall, so those fail the provider
# immediately instead of burning a request per key to prove it.
_KEY_EXHAUSTED_STATUSES = {401, 403, 429}


async def _post(payload: dict) -> Optional[dict]:
    """One Tavily call, rotating across every configured key.

    Returns None only when no key is configured at all. Raises ProviderError
    once every key has been tried and failed, so the cascade in web.py moves
    on to the next provider (and its circuit breaker can tell "broken" apart
    from "no hits" — see app/search/errors.py).
    """
    keys = settings.tavily_api_keys
    if not keys:
        logger.warning("Tavily search requested but TAVILY_API_KEY is empty")
        return None

    # Skip keys already known to be spent, keeping their order. filter_open
    # hands back the full list if EVERY key is tripped, so a stale breaker
    # can never make this give up without trying.
    live_ids = set(circuit_breaker.filter_open([_breaker_id(i) for i in range(len(keys))]))
    candidates = [(i, k) for i, k in enumerate(keys) if _breaker_id(i) in live_ids]

    last_error: Optional[Exception] = None
    for index, api_key in candidates:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    _SEARCH_URL, json=payload, headers=_headers(api_key)
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:200]
            status = e.response.status_code
            if status in _KEY_EXHAUSTED_STATUSES:
                logger.warning(
                    "Tavily key %d/%d rejected (HTTP %s) — trying the next key",
                    index + 1, len(keys), status,
                )
                circuit_breaker.record_failure(_breaker_id(index))
                last_error = e
                continue
            # A server-side problem: another key would hit the same wall.
            raise ProviderError(f"Tavily search failed: HTTP {status} {body}") from e
        except Exception as e:
            raise ProviderError(f"Tavily search failed: {e}") from e

        circuit_breaker.record_success(_breaker_id(index))
        return response.json()

    raise ProviderError(
        f"Tavily search failed: all {len(keys)} configured key(s) exhausted "
        f"or rejected (last: {last_error})"
    )


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
    return bool(settings.tavily_api_keys)
