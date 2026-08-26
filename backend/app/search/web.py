"""The one door to the web.

Every caller that wants a web result imports from here, never from a provider
module. Two providers exist:

    tavily   — the default. Hosted, LLM-shaped extracts, one HTTPS call.
    searxng  — the previous default. Self-hosted at SEARXNG_URL, keeps the
               query on the machine.

⚠ **The choice is a privacy decision, not a quality one.** SearXNG runs in the
compose stack, so a query never left the host; Tavily is a third party and the
query string does. Neither ever sees paper text, chunks, or chat history — the
callers pass a query and nothing else — but "nothing leaves this machine" is
only literally true on searxng. WEB_SEARCH_PROVIDER exists so that trade can be
taken back with one line in .env.
"""

from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.search import searxng_client, tavily_client

logger = get_logger(__name__)

_TAVILY = "tavily"
_SEARXNG = "searxng"
_NONE = "none"


def active_provider() -> str:
    """Which provider serves this call: ``tavily``, ``searxng``, or ``none``.

    Resolved per call rather than cached at import so a key added to .env takes
    effect on the next request instead of the next restart.
    """
    pinned = (settings.web_search_provider or "auto").strip().lower()
    if pinned in (_TAVILY, _SEARXNG, _NONE):
        return pinned
    # auto: a configured key means the operator chose the hosted provider.
    if settings.tavily_api_key:
        return _TAVILY
    if settings.searxng_url:
        return _SEARXNG
    return _NONE


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Web results as ``{title, url, snippet, source_engine, score}``.

    Returns ``[]`` rather than raising on every failure path — a dead search
    provider degrades an answer, it does not break the request.
    """
    provider = active_provider()
    if provider == _TAVILY:
        return await tavily_client.search(query, categories=categories, limit=limit)
    if provider == _SEARXNG:
        return await searxng_client.search(query, categories=categories, limit=limit)
    logger.warning("web search requested with no provider configured")
    return []


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Image results as ``{img_url, thumbnail, title, source_url, source_engine}``."""
    provider = active_provider()
    if provider == _TAVILY:
        return await tavily_client.search_images(query, limit=limit)
    if provider == _SEARXNG:
        return await searxng_client.search_images(query, limit=limit)
    return []


async def is_available() -> bool:
    """Whether the active provider can answer right now."""
    provider = active_provider()
    if provider == _TAVILY:
        return await tavily_client.is_available()
    if provider == _SEARXNG:
        return await searxng_client.is_available()
    return False


def is_configured() -> bool:
    """Whether a provider is configured at all — no network call.

    Used on hot paths (the paper agent decides whether to offer the WEB tool on
    every question) where paying for a live probe per request is not affordable.
    """
    return active_provider() != _NONE
