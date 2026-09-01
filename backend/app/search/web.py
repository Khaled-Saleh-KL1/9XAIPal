"""The one door to the web.

Every caller that wants a web result imports from here, never from a
provider module directly. Five providers exist, tried in this fixed order:

⚠ Google was removed 2026-09-01. It was tried twice — Gemini Search
grounding (free tier unavailable in the EEA, so 100% 429 from this host)
and then the Custom Search JSON API (works, but only 100 free queries/day
before it bills). Neither was worth a slot: the deliberate decision is that
no provider here should be able to cost money, and Tavily's per-key monthly
allowance scales by adding keys instead.

    tavily      — LLM-shaped extracts, one HTTPS call. First in the
                  cascade. Accepts a COMMA-SEPARATED list of keys and
                  rotates across them: each Tavily key carries its own free
                  monthly allowance, so an exhausted key falls to the next
                  before the cascade moves on to another provider at all.
    linkup      — real page content per result, plus image search.
    exa         — neural/semantic search, strong on academic sources.
    serpapi     — genuine Google SERP data via a paid scraping API.
    duckduckgo  — the `ddgs` library, no API key, no quota. Always
                  "configured": the one provider that's never unavailable
                  for lack of credentials. Last, by design — it scrapes an
                  undocumented endpoint (no official DDG search API), so
                  it's also the least reliable of the six.

Providers that fail repeatedly are skipped for a cooldown by
app/core/circuit_breaker.py — their PRIORITY never changes, so one that
recovers is picked up again automatically. This is what keeps a
permanently-dead provider (a revoked key, an exhausted quota) from costing
a network round-trip and an ERROR log on every single request while it
still sits first in the configured order.

"auto" (the default) tries each configured provider in order and falls
through to the next the moment one errors OR returns zero results — a
single provider being down, out of quota, or rate-limited never means the
request comes back empty as long as one of the others can still answer.
Because duckduckgo needs no configuration, `is_configured()` is True under
"auto" even with every API key left blank — web search is only truly off
when WEB_SEARCH_PROVIDER is explicitly set to "none".

This replaced SearXNG (self-hosted, removed 2026-08-31): SearXNG's own
`score` field is a per-engine position weight, not a comparable relevance
score, which silently produced irrelevant results whenever a category
filter was applied — see git history on external_context.py for the
concrete case (a "FlashAttention 2 paper" query returning MDN docs and
unrelated Docker Hub images ahead of the actual paper). None of the five
providers here have that failure mode: none accept a category filter to
begin with (the domain bias lives entirely in
`external_context.rewrite_query_for_papers` instead, which works for all
five), and cascading on a bad result is strictly safer than trusting one
provider's internal ranking blind.
"""

from typing import Optional

from app.core import circuit_breaker
from app.core.config import settings
from app.core.logging import get_logger
from app.search.errors import ProviderError
from app.search import (
    duckduckgo_client,
    exa_client,
    linkup_client,
    serpapi_client,
    tavily_client,
)

logger = get_logger(__name__)

_NONE = "none"

# Cascade order. Index 0 is tried first on "auto"; a pin to one name skips
# straight to that entry with no fallback. duckduckgo is last and needs no
# key — see module docstring.
_PROVIDERS = [
    ("tavily", tavily_client),
    ("linkup", linkup_client),
    ("exa", exa_client),
    ("serpapi", serpapi_client),
    ("duckduckgo", duckduckgo_client),
]


def _configured_names() -> list[str]:
    """Providers with credentials present, in cascade order. No network I/O.

    duckduckgo needs no credentials, so it's always in this list.
    """
    checks = {
        # Any one of the comma-separated Tavily keys is enough to count.
        "tavily": bool(settings.tavily_api_keys),
        "linkup": bool(settings.linkup_api_key),
        "exa": bool(settings.exa_api_key),
        "serpapi": bool(settings.serpapi_api_key),
        "duckduckgo": True,
    }
    return [name for name, _ in _PROVIDERS if checks[name]]


def _cascade() -> list[tuple[str, object]]:
    """The provider list to actually try for this call, honoring a pin.

    Under "auto", providers whose circuit breaker is open (repeatedly failing
    — see app/core/circuit_breaker.py) are skipped so a known-dead provider
    stops costing a round-trip on every request. Their PRIORITY is unchanged:
    the moment one recovers it goes back to being tried in its configured
    position. A pin deliberately bypasses the breaker — pinning exists to
    watch one specific provider, including watching it fail.
    """
    pinned = (settings.web_search_provider or "auto").strip().lower()
    if pinned == _NONE:
        return []
    if pinned == "auto":
        names = set(circuit_breaker.filter_open(_configured_names()))
        return [(name, client) for name, client in _PROVIDERS if name in names]
    # Pinned to one specific provider — no fallback, matches the pre-cascade
    # behavior for debugging a single provider in isolation.
    for name, client in _PROVIDERS:
        if name == pinned:
            return [(name, client)]
    logger.warning(f"WEB_SEARCH_PROVIDER={pinned!r} is not a known provider")
    return []


def active_provider() -> str:
    """The provider that would be tried first right now — a log/health label.

    Resolved per call rather than cached at import so a key added to .env
    takes effect on the next request instead of the next restart. With a
    cascade, this names only the FIRST provider that would be attempted, not
    necessarily the one that ends up serving any specific query — see
    `configured_providers()` for the full list.
    """
    cascade = _cascade()
    return cascade[0][0] if cascade else _NONE


def configured_providers() -> list[str]:
    """Every provider that would be tried, in order — for the /health payload."""
    return [name for name, _ in _cascade()]


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Web results as ``{title, url, snippet, source_engine, score}``.

    Tries each configured provider in cascade order; falls through to the
    next on any exception or an empty result list. Returns ``[]`` only once
    every configured provider has been tried and none produced anything —
    a dead or exhausted provider degrades an answer, it does not break the
    request.
    """
    for name, client in _cascade():
        try:
            results = await client.search(query, categories=categories, limit=limit)
        except ProviderError as e:
            logger.warning(f"{name} search failed, falling through: {e}")
            circuit_breaker.record_failure(name)
            continue
        except Exception as e:
            # A bug in a client, not a provider failure it declared. Still
            # counted: from here it is indistinguishable from being broken.
            logger.exception(f"{name} search raised unexpectedly, falling through: {e}")
            circuit_breaker.record_failure(name)
            continue
        if results:
            circuit_breaker.record_success(name)
            return results
        # Empty means the provider worked and found nothing (clients raise
        # ProviderError for real failures). Fall through to try a provider
        # with a different index, but do NOT penalize this one — tripping a
        # healthy provider over an obscure query would skip it for every
        # later search.
        logger.info(f"{name} returned no results, falling through")
    return []


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Image results as ``{img_url, thumbnail, title, source_url, source_engine}``.

    ⚠ exa always returns ``[]`` here by design (it has no image endpoint —
    it indexes documents, not media; see its client). That is an empty result, not a
    failure, so it never counts against them in the circuit breaker: a
    provider skipped for images stays first in line for text.
    """
    for name, client in _cascade():
        try:
            results = await client.search_images(query, limit=limit)
        except ProviderError as e:
            logger.warning(f"{name} image search failed, falling through: {e}")
            circuit_breaker.record_failure(name)
            continue
        except Exception as e:
            logger.exception(f"{name} image search raised unexpectedly, falling through: {e}")
            circuit_breaker.record_failure(name)
            continue
        if results:
            circuit_breaker.record_success(name)
            return results
    return []


async def is_available() -> bool:
    """Whether at least one configured provider can answer right now."""
    for name, client in _cascade():
        try:
            if await client.is_available():
                return True
        except Exception:
            continue
    return False


def is_configured() -> bool:
    """Whether at least one provider is configured at all — no network call.

    Used on hot paths (the paper agent decides whether to offer the WEB tool
    on every question) where paying for a live probe per request is not
    affordable. True under "auto" even with every API key blank, since
    duckduckgo needs none — only an explicit WEB_SEARCH_PROVIDER=none (or a
    pin to a provider with no key) makes this False.
    """
    return bool(_cascade())
