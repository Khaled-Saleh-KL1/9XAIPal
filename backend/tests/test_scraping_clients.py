"""Unit tests for the scraping client modules' own request/response parsing
(app/scraping/firecrawl_client.py, crw_client.py) — the cascade behavior
those feed into is covered separately in test_article_fetch_cascade.py,
which mocks these functions out entirely. These tests exercise the actual
JSON parsing, in particular the metadata.contentType -> is_pdf detection
that fetch_resource() relies on to route a PDF URL correctly (see
firecrawl_client.fetch_html's docstring for the bug this fixes).
"""

import httpx
import pytest

from app.core.config import settings
from app.scraping import crw_client, firecrawl_client
from app.scraping.errors import FetchProviderError


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json = json_body
        self.text = str(json_body)

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "https://example.com")
            raise httpx.HTTPStatusError("x", request=req, response=httpx.Response(self.status_code, request=req))

    def json(self):
        return self._json


@pytest.fixture
def firecrawl_key(monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_api_key", "fc-key")


@pytest.fixture
def crw_key(monkeypatch):
    monkeypatch.setattr(settings, "crw_api_key", "crw-key")


# ── firecrawl_client ─────────────────────────────────────────────────────────

def test_firecrawl_fetch_html_normal_page_is_not_flagged_as_pdf(firecrawl_key, monkeypatch):
    body = {
        "success": True,
        "data": {
            "html": "<html>hi</html>",
            "metadata": {"sourceURL": "https://example.com/x", "contentType": "text/html; charset=utf-8"},
        },
    }
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: _FakeResponse(200, body))

    html, final_url, is_pdf = firecrawl_client.fetch_html("https://example.com/x")

    assert html == "<html>hi</html>"
    assert final_url == "https://example.com/x"
    assert is_pdf is False


def test_firecrawl_fetch_html_flags_pdf_content_type(firecrawl_key, monkeypatch):
    """The actual bug: an arXiv /pdf/ link has no .pdf in its URL at all —
    metadata.contentType is the only signal, confirmed against a real
    Firecrawl response for https://arxiv.org/pdf/2605.27295."""
    body = {
        "success": True,
        "data": {
            "html": "<html>lossy pdf-to-html conversion</html>",
            "metadata": {
                "sourceURL": "https://arxiv.org/pdf/2605.27295",
                "contentType": "application/pdf",
                "numPages": 21,
            },
        },
    }
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: _FakeResponse(200, body))

    html, final_url, is_pdf = firecrawl_client.fetch_html("https://arxiv.org/pdf/2605.27295")

    assert is_pdf is True
    assert final_url == "https://arxiv.org/pdf/2605.27295"


def test_firecrawl_fetch_html_raises_on_no_html(firecrawl_key, monkeypatch):
    body = {"success": True, "data": {"metadata": {}}}
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: _FakeResponse(200, body))

    with pytest.raises(FetchProviderError, match="no html"):
        firecrawl_client.fetch_html("https://example.com/x")


def test_firecrawl_fetch_html_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_api_key", "")
    with pytest.raises(FetchProviderError, match="not configured"):
        firecrawl_client.fetch_html("https://example.com/x")


def test_firecrawl_fetch_html_exhausted_key_status(firecrawl_key, monkeypatch):
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: _FakeResponse(402, {}))
    with pytest.raises(FetchProviderError, match="rejected/exhausted"):
        firecrawl_client.fetch_html("https://example.com/x")


# ── crw_client (same response shape, confirmed Firecrawl-compatible) ────────

def test_crw_fetch_html_flags_pdf_content_type(crw_key, monkeypatch):
    body = {
        "success": True,
        "data": {
            "html": "<html>lossy</html>",
            "metadata": {"sourceURL": "https://arxiv.org/pdf/2605.27295", "contentType": "application/pdf"},
        },
    }
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: _FakeResponse(200, body))

    html, final_url, is_pdf = crw_client.fetch_html("https://arxiv.org/pdf/2605.27295")

    assert is_pdf is True


def test_crw_fetch_html_missing_content_type_defaults_to_not_pdf(crw_key, monkeypatch):
    """If CRW's response ever lacks the field entirely, this must not crash
    and must not falsely flag a normal page as a PDF."""
    body = {"success": True, "data": {"html": "<html>hi</html>", "metadata": {}}}
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: _FakeResponse(200, body))

    _, _, is_pdf = crw_client.fetch_html("https://example.com/x")

    assert is_pdf is False
