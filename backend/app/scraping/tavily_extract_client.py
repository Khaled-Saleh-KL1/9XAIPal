"""Tavily's /extract endpoint — the last-resort tier in article fetching,
tried only once every HTML-capable provider (Firecrawl, CRW, and this box's
own direct fetch — see app/services/article_extraction.py::fetch_resource)
has failed. Reuses the *same* TAVILY_API_KEY(s) already configured for web
search (app/search/tavily_client.py), rotated the same way.

Sync throughout — see firecrawl_client.py's module docstring for why (this
only ever runs inside the Celery worker, never an async request handler;
app/search/tavily_client.py is a DIFFERENT, async module for a DIFFERENT
caller — FastAPI's web-search path — despite the similar name and shared
keys).

⚠ Structurally different from the other two article-fetch providers: Tavily
Extract returns already-extracted `raw_content` (markdown or plain text),
never the page's actual HTML. That means it can feed a caller an article's
text directly, but it can never feed the raw-snapshot crawler
(services/article_crawl.py), which needs real HTML to sanitize and store —
this module deliberately isn't part of fetch_resource()'s cascade for that
reason, only extract_article()'s own final fallback.

Returns a plain (title, markdown, asset_map) tuple rather than
article_extraction.ArticleExtraction directly, so this module has no import
dependency on article_extraction.py at all — the same one-way-dependency
shape every app/search/*_client.py already has toward its caller.
"""

from typing import Optional

import httpx

from app.core import circuit_breaker
from app.core.config import settings
from app.core.logging import get_logger
from app.scraping.errors import FetchProviderError

logger = get_logger(__name__)

_EXTRACT_URL = "https://api.tavily.com/extract"

# Actually rendering + extracting a page can take longer than a search call;
# Tavily's own ceiling on this endpoint is 60s.
_TIMEOUT = 65.0

# Same reasoning as search/tavily_client.py's _KEY_EXHAUSTED_STATUSES.
_KEY_EXHAUSTED_STATUSES = {401, 403, 429}


def _breaker_id(index: int) -> str:
    """Deliberately a DIFFERENT breaker namespace from search/tavily_client's
    `tavily#{i}` — whether Tavily pools one credit balance across search and
    extract per key isn't confirmed either way, and wrongly assuming they
    share a breaker could skip a key here that's still perfectly good for
    this endpoint (or vice versa)."""
    return f"tavily-extract#{index}"


def _title_from_markdown(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:200]
    return fallback


def extract(url: str) -> tuple[str, str, dict]:
    """Extract `url`'s content via Tavily and return (title, markdown, asset_map).

    Raises FetchProviderError if no key is configured, every key is
    exhausted/rejected, or Tavily reports this specific URL as failed.
    """
    keys = settings.tavily_api_keys
    if not keys:
        raise FetchProviderError("Tavily not configured (TAVILY_API_KEY unset)")

    live_ids = set(circuit_breaker.filter_open([_breaker_id(i) for i in range(len(keys))]))
    candidates = [(i, k) for i, k in enumerate(keys) if _breaker_id(i) in live_ids]

    last_error: Optional[Exception] = None
    for index, api_key in candidates:
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                response = client.post(
                    _EXTRACT_URL,
                    json={"urls": [url], "format": "markdown", "include_images": True},
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in _KEY_EXHAUSTED_STATUSES:
                logger.warning(
                    "Tavily Extract key %d/%d rejected (HTTP %s) — trying the next key",
                    index + 1, len(keys), status,
                )
                circuit_breaker.record_failure(_breaker_id(index))
                last_error = e
                continue
            raise FetchProviderError(f"Tavily Extract failed: HTTP {status}") from e
        except Exception as e:
            raise FetchProviderError(f"Tavily Extract failed: {e}") from e

        body = response.json()
        results = body.get("results") or []
        if not results:
            failed = body.get("failed_results") or []
            reason = failed[0].get("error") if failed else "no results"
            circuit_breaker.record_success(_breaker_id(index))  # the KEY worked; this URL didn't
            raise FetchProviderError(f"Tavily Extract could not extract this page: {reason}")

        circuit_breaker.record_success(_breaker_id(index))
        result = results[0]
        markdown = result.get("raw_content") or ""
        if not markdown.strip():
            raise FetchProviderError("Tavily Extract returned empty content")

        asset_map = {}
        for img_url in result.get("images") or []:
            if isinstance(img_url, str) and img_url:
                asset_map[img_url.rsplit("/", 1)[-1]] = img_url

        title = _title_from_markdown(markdown, fallback=url)
        return title, markdown, asset_map

    raise FetchProviderError(
        f"Tavily Extract failed: all {len(keys)} configured key(s) exhausted or rejected "
        f"(last: {last_error})"
    )


def is_configured() -> bool:
    return bool(settings.tavily_api_keys)
