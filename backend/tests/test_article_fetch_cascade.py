"""Unit tests for article_extraction.fetch_resource()'s managed-API cascade
(Firecrawl -> CRW -> this box's own direct fetch) and the separate,
last-resort Tavily Extract tier (try_tavily_extract_fallback).

Same shape as test_web_search_cascade.py's tests for app/search/web.py — a
provider errors, the cascade falls through, transparently to the caller —
just SYNC throughout (no AsyncMock needed): this module runs inside the
Celery worker, never an async request handler, and neither do its clients
(see article_extraction.py's module docstring for why).
"""

from unittest.mock import patch

import pytest

from app.core import circuit_breaker
from app.core.config import settings
from app.scraping import crw_client, firecrawl_client, tavily_extract_client
from app.scraping.errors import FetchProviderError
from app.services import article_extraction
from app.services.article_extraction import (
    ArticleExtractionError,
    fetch_resource,
    try_tavily_extract_fallback,
)

_URL = "https://example.com/some-article"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """No managed providers configured by default, breaker reset around
    every test — same reasoning as test_web_search_cascade.py's fixture:
    the circuit breaker is module-level state that would otherwise leak
    a trip from one test into the next."""
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "firecrawl_api_key", "")
    monkeypatch.setattr(settings, "crw_api_key", "")
    monkeypatch.setattr(settings, "tavily_api_key", "")
    yield
    circuit_breaker.reset()


# ── fetch_resource cascade ───────────────────────────────────────────────────

def test_fetch_resource_uses_firecrawl_when_configured():
    with patch.object(settings, "firecrawl_api_key", "fc-key"), \
         patch.object(firecrawl_client, "fetch_html", return_value=("<html>hi</html>", _URL)) as fc, \
         patch.object(crw_client, "fetch_html") as crw, \
         patch.object(article_extraction, "_fetch_direct") as direct:
        resource = fetch_resource(_URL)

    assert resource.content == b"<html>hi</html>"
    assert resource.content_type == "text/html"
    fc.assert_called_once()
    crw.assert_not_called()
    direct.assert_not_called()


def test_fetch_resource_falls_through_to_crw_on_firecrawl_failure():
    with patch.object(settings, "firecrawl_api_key", "fc-key"), \
         patch.object(settings, "crw_api_key", "crw-key"), \
         patch.object(firecrawl_client, "fetch_html", side_effect=FetchProviderError("down")), \
         patch.object(crw_client, "fetch_html", return_value=("<html>crw</html>", _URL)) as crw, \
         patch.object(article_extraction, "_fetch_direct") as direct:
        resource = fetch_resource(_URL)

    assert resource.content == b"<html>crw</html>"
    crw.assert_called_once()
    direct.assert_not_called()


def test_fetch_resource_falls_through_to_direct_when_both_managed_fail():
    from app.services.article_extraction import FetchedResource

    with patch.object(settings, "firecrawl_api_key", "fc-key"), \
         patch.object(settings, "crw_api_key", "crw-key"), \
         patch.object(firecrawl_client, "fetch_html", side_effect=FetchProviderError("down")), \
         patch.object(crw_client, "fetch_html", side_effect=FetchProviderError("also down")), \
         patch.object(article_extraction, "_fetch_direct") as direct:
        direct.return_value = FetchedResource(content=b"direct", content_type="text/html", final_url=_URL)
        resource = fetch_resource(_URL)

    assert resource.content == b"direct"
    direct.assert_called_once()


def test_fetch_resource_skips_unconfigured_providers():
    """Only CRW has a key — Firecrawl must not even be attempted."""
    with patch.object(settings, "crw_api_key", "crw-key"), \
         patch.object(firecrawl_client, "fetch_html") as fc, \
         patch.object(crw_client, "fetch_html", return_value=("<html>crw</html>", _URL)) as crw:
        resource = fetch_resource(_URL)

    assert resource.content == b"<html>crw</html>"
    fc.assert_not_called()
    crw.assert_called_once()


def test_fetch_resource_raises_original_direct_fetch_error_when_everything_fails():
    with patch.object(settings, "firecrawl_api_key", "fc-key"), \
         patch.object(firecrawl_client, "fetch_html", side_effect=FetchProviderError("down")), \
         patch.object(
             article_extraction, "_fetch_direct",
             side_effect=ArticleExtractionError("direct fetch also failed: 403"),
         ):
        with pytest.raises(ArticleExtractionError, match="direct fetch also failed"):
            fetch_resource(_URL)


def test_ssrf_guard_runs_before_any_provider_is_tried():
    """A private/loopback URL must be rejected outright — never handed to a
    managed provider (spending a credit on something that was never going
    to be a real article regardless of who fetched it)."""
    with patch.object(settings, "firecrawl_api_key", "fc-key"), \
         patch.object(settings, "crw_api_key", "crw-key"), \
         patch.object(firecrawl_client, "fetch_html") as fc, \
         patch.object(crw_client, "fetch_html") as crw:
        with pytest.raises(ArticleExtractionError):
            fetch_resource("http://127.0.0.1/admin")

    fc.assert_not_called()
    crw.assert_not_called()


# ── Circuit breaker ──────────────────────────────────────────────────────────

def test_repeated_firecrawl_failures_trip_the_breaker_and_skip_it():
    with patch.object(settings, "firecrawl_api_key", "fc-key"), \
         patch.object(settings, "crw_api_key", "crw-key"), \
         patch.object(firecrawl_client, "fetch_html", side_effect=FetchProviderError("down")) as fc, \
         patch.object(crw_client, "fetch_html", return_value=("<html>crw</html>", _URL)):

        for _ in range(circuit_breaker.FAILURE_THRESHOLD):
            fetch_resource(_URL)

        calls_before = fc.call_count
        fetch_resource(_URL)  # breaker now open — firecrawl must be skipped entirely
        assert fc.call_count == calls_before
        assert circuit_breaker.is_open("firecrawl") is True


def test_empty_is_not_a_concept_here_only_exceptions_trip_the_breaker():
    """Unlike web search, a fetch either produces html or raises — there's no
    'worked but empty' case to distinguish, so every FetchProviderError trips
    the breaker exactly like search's ProviderError does."""
    with patch.object(settings, "firecrawl_api_key", "fc-key"), \
         patch.object(settings, "crw_api_key", "crw-key"), \
         patch.object(firecrawl_client, "fetch_html", side_effect=FetchProviderError("down")), \
         patch.object(crw_client, "fetch_html", return_value=("<html>crw</html>", _URL)):

        for _ in range(circuit_breaker.FAILURE_THRESHOLD):
            fetch_resource(_URL)

    assert circuit_breaker.is_open("firecrawl") is True
    assert circuit_breaker.is_open("crw") is False


# ── Tavily Extract: last-resort tier ────────────────────────────────────────

def test_tavily_extract_fallback_builds_an_article_extraction():
    with patch.object(
        tavily_extract_client, "extract",
        return_value=("A Real Title", "# A Real Title\n\nSome content.", {"fig.png": "https://x/fig.png"}),
    ):
        article = try_tavily_extract_fallback(_URL)

    assert article is not None
    assert article.title == "A Real Title"
    assert "Some content." in article.markdown
    assert article.asset_map == {"fig.png": "https://x/fig.png"}


def test_tavily_extract_fallback_returns_none_on_failure_not_raise():
    with patch.object(tavily_extract_client, "extract", side_effect=FetchProviderError("no key")):
        assert try_tavily_extract_fallback(_URL) is None


def test_tavily_extract_key_rotation_falls_through_to_next_key(monkeypatch):
    """Mirrors search/tavily_client.py's own rotation test shape — an
    exhausted/rejected key moves to the next configured one before giving up."""
    import httpx as httpx_mod

    monkeypatch.setattr(settings, "tavily_api_key", "key-a,key-b")
    circuit_breaker.reset()

    calls = {"n": 0}

    class _FakeResp:
        def __init__(self, status):
            self.status_code = status
        def raise_for_status(self):
            if self.status_code >= 400:
                req = httpx_mod.Request("POST", "https://api.tavily.com/extract")
                resp = httpx_mod.Response(self.status_code, request=req)
                raise httpx_mod.HTTPStatusError("x", request=req, response=resp)
        def json(self):
            return {"results": [{"url": _URL, "raw_content": "content from key b", "images": []}]}

    def fake_post(self, url, json=None, headers=None):
        calls["n"] += 1
        if headers["Authorization"] == "Bearer key-a":
            return _FakeResp(429)
        return _FakeResp(200)

    with patch("httpx.Client.post", fake_post):
        title, markdown, _ = tavily_extract_client.extract(_URL)

    assert markdown == "content from key b"
    assert calls["n"] == 2
