"""CRW (fastcrw.com) client — a self-described "Firecrawl-compatible" managed
scrape API, confirmed via its own OpenAPI spec: /v1/scrape's success response
is `{"success": true, "data": {"markdown", "html", "metadata", ...}}`, the
same shape Firecrawl's own /v2/scrape returns. Second in the article-fetch
cascade (see app/services/article_extraction.py::fetch_resource) — tried
after Firecrawl, before falling back to this box's own direct fetch.

Sync throughout — see firecrawl_client.py's module docstring for why (this
only ever runs inside the Celery worker, never an async request handler).

⚠ This provider is a network egress, like every search client in
app/search/: the URL being imported leaves for api.fastcrw.com.
"""

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.scraping.errors import FetchProviderError

logger = get_logger(__name__)

_SCRAPE_URL = "https://api.fastcrw.com/v1/scrape"

# Same reasoning as firecrawl_client.py: a managed scrape can involve
# actually rendering the page, meaningfully slower than a plain fetch.
_TIMEOUT = 75.0

_EXHAUSTED_STATUSES = {401, 402, 429}


def fetch_html(url: str) -> tuple[str, str]:
    """Fetch `url` via CRW's /v1/scrape and return (html, final_url).

    Raises FetchProviderError on any failure. See firecrawl_client.fetch_html
    for the shared reasoning — this is CRW's counterpart, same contract.
    """
    api_key = settings.crw_api_key
    if not api_key:
        raise FetchProviderError("CRW not configured (CRW_API_KEY unset)")

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.post(
                _SCRAPE_URL,
                json={"url": url, "formats": ["html"]},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        body = (e.response.text or "")[:200]
        if status in _EXHAUSTED_STATUSES:
            raise FetchProviderError(f"CRW key rejected/exhausted: HTTP {status}") from e
        raise FetchProviderError(f"CRW scrape failed: HTTP {status} {body}") from e
    except Exception as e:
        raise FetchProviderError(f"CRW scrape failed: {e}") from e

    body = response.json()
    if not body.get("success"):
        raise FetchProviderError(f"CRW reported failure: {body.get('error', body)}")

    data = body.get("data") or {}
    html = data.get("html")
    if not html:
        raise FetchProviderError("CRW response carried no html")

    final_url = (data.get("metadata") or {}).get("sourceURL") or url
    return html, final_url


def is_configured() -> bool:
    return bool(settings.crw_api_key)
