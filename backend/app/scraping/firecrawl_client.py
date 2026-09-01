"""Firecrawl (https://firecrawl.dev) client — a managed scrape API that runs
a real browser on its own infrastructure. First in the article-fetch
cascade (see app/services/article_extraction.py::fetch_resource): it can get
past a JS challenge (Cloudflare, etc.) that a plain HTTP client never can,
which is the actual reason this cascade exists — see the Medium/Cloudflare
case in git history.

Sync throughout, deliberately NOT matching app/search/*_client.py's async
style: article_extraction.py's whole call chain runs inside the Celery
worker (see core/celery_app.py's module docstring on why everything there is
sync), never from an async FastAPI request handler. There is no caller this
would ever need to be awaited from.

⚠ This provider is a network egress, like every search client in
app/search/: the URL being imported leaves for api.firecrawl.dev. Nothing
about the reader's paper library, chat history, or account leaves with it —
only the one URL.
"""

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.scraping.errors import FetchProviderError

logger = get_logger(__name__)

_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"

# A managed scrape can involve actually rendering the page (waiting on JS,
# sometimes a bot-challenge) — meaningfully slower than a plain fetch.
# Firecrawl's own default processing timeout is 60s; this leaves headroom
# above that rather than racing it.
_TIMEOUT = 75.0

# HTTP statuses that mean "this key/account is done" — 401 (bad key), 402
# (out of credits), 429 (rate limited) — as opposed to a real server error,
# which no key would get past.
_EXHAUSTED_STATUSES = {401, 402, 429}


def fetch_html(url: str) -> tuple[str, str]:
    """Fetch `url` via Firecrawl's /v2/scrape and return (html, final_url).

    Raises FetchProviderError on any failure — unconfigured (no key), a
    rejected/exhausted key, a server error, or a response that didn't carry
    the html this needs. The caller (fetch_resource's cascade) falls through
    to the next provider either way.
    """
    api_key = settings.firecrawl_api_key
    if not api_key:
        raise FetchProviderError("Firecrawl not configured (FIRECRAWL_API_KEY unset)")

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
            raise FetchProviderError(f"Firecrawl key rejected/exhausted: HTTP {status}") from e
        raise FetchProviderError(f"Firecrawl scrape failed: HTTP {status} {body}") from e
    except Exception as e:
        raise FetchProviderError(f"Firecrawl scrape failed: {e}") from e

    body = response.json()
    if not body.get("success"):
        raise FetchProviderError(f"Firecrawl reported failure: {body.get('error', body)}")

    data = body.get("data") or {}
    html = data.get("html")
    if not html:
        raise FetchProviderError("Firecrawl response carried no html")

    final_url = (data.get("metadata") or {}).get("sourceURL") or url
    return html, final_url


def is_configured() -> bool:
    return bool(settings.firecrawl_api_key)
