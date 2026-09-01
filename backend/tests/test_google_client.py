"""Unit tests for app.search.google_client (Custom Search JSON API).

Covers the response parsing for both text and image mode, the failure
contract (raise ProviderError, don't return []), and — the part that keeps
this deployment free — that a spent daily quota skips the provider quietly
instead of calling Google anyway or looking like a provider failure.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import settings
from app.search import google_client, quota
from app.search.errors import ProviderError


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Fully configured, with quota always available unless a test says
    otherwise — so quota never silently masks a parsing test."""
    monkeypatch.setattr(settings, "google_api_key", "AIzaSy-test-key")
    monkeypatch.setattr(settings, "google_search_cx", "test-cx")
    monkeypatch.setattr(settings, "google_search_daily_limit", 100)
    monkeypatch.setattr(quota, "try_consume", AsyncMock(return_value=True))


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _mock_get(data):
    async def fake_get(*args, **kwargs):
        return _FakeResponse(data)
    return fake_get


async def test_parses_text_results():
    data = {
        "items": [
            {
                "title": "FlashAttention-2 - arXiv",
                "link": "https://arxiv.org/abs/2307.08691",
                "snippet": "Faster attention with better parallelism.",
            },
            {"title": "OpenReview", "link": "https://openreview.net/forum?id=x", "snippet": "Review."},
        ]
    }
    with patch.object(httpx.AsyncClient, "get", _mock_get(data)):
        results = await google_client.search("query")

    assert len(results) == 2
    assert results[0] == {
        "title": "FlashAttention-2 - arXiv",
        "url": "https://arxiv.org/abs/2307.08691",
        "snippet": "Faster attention with better parallelism.",
        "source_engine": "google",
        "score": None,
    }


async def test_parses_image_results():
    data = {
        "items": [
            {
                "title": "A diagram",
                "link": "https://example.com/diagram.png",
                "image": {
                    "thumbnailLink": "https://example.com/thumb.png",
                    "contextLink": "https://example.com/article",
                },
            }
        ]
    }
    with patch.object(httpx.AsyncClient, "get", _mock_get(data)):
        results = await google_client.search_images("query")

    assert results == [{
        "img_url": "https://example.com/diagram.png",
        "thumbnail": "https://example.com/thumb.png",
        "title": "A diagram",
        # The hosting page, not the image file — what the reader clicks.
        "source_url": "https://example.com/article",
        "source_engine": "google",
    }]


async def test_no_items_is_an_empty_result_not_a_failure():
    with patch.object(httpx.AsyncClient, "get", _mock_get({})):
        assert await google_client.search("query") == []


async def test_http_error_raises_provider_error():
    async def raising_get(*args, **kwargs):
        raise httpx.HTTPStatusError("403", request=None, response=httpx.Response(403, text="denied"))
    with patch.object(httpx.AsyncClient, "get", raising_get):
        with pytest.raises(ProviderError):
            await google_client.search("query")


# ── Configuration and quota: the "never pay" guarantees ──────────────────

async def test_missing_cx_skips_without_calling(monkeypatch):
    """Custom Search cannot run on a key alone — don't spend a request
    discovering that."""
    monkeypatch.setattr(settings, "google_search_cx", "")
    get_mock = AsyncMock()
    with patch.object(httpx.AsyncClient, "get", get_mock):
        assert await google_client.search("query") == []
    get_mock.assert_not_called()


async def test_missing_key_skips_without_calling(monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", "")
    get_mock = AsyncMock()
    with patch.object(httpx.AsyncClient, "get", get_mock):
        assert await google_client.search("query") == []
    get_mock.assert_not_called()


async def test_spent_quota_skips_without_calling_google(monkeypatch):
    """The core money guarantee: once the daily allowance is gone, no
    request goes out at all."""
    monkeypatch.setattr(quota, "try_consume", AsyncMock(return_value=False))
    get_mock = AsyncMock()
    with patch.object(httpx.AsyncClient, "get", get_mock):
        assert await google_client.search("query") == []
    get_mock.assert_not_called()


async def test_spent_quota_returns_empty_rather_than_raising(monkeypatch):
    """A spent quota is the provider working as designed, NOT a failure —
    raising would trip the circuit breaker and make it look broken."""
    monkeypatch.setattr(quota, "try_consume", AsyncMock(return_value=False))
    with patch.object(httpx.AsyncClient, "get", AsyncMock()):
        assert await google_client.search("query") == []
        assert await google_client.search_images("query") == []


async def test_every_call_reserves_quota_first(monkeypatch):
    consume = AsyncMock(return_value=True)
    monkeypatch.setattr(quota, "try_consume", consume)
    with patch.object(httpx.AsyncClient, "get", _mock_get({"items": []})):
        await google_client.search("query")
        await google_client.search_images("query")
    assert consume.await_count == 2, "text and image search must each reserve"


async def test_text_and_images_share_one_quota_budget(monkeypatch):
    """Google counts both against the same 100/day, so this must too."""
    consume = AsyncMock(return_value=True)
    monkeypatch.setattr(quota, "try_consume", consume)
    with patch.object(httpx.AsyncClient, "get", _mock_get({"items": []})):
        await google_client.search("query")
        await google_client.search_images("query")
    providers = {call.args[0] for call in consume.await_args_list}
    assert providers == {"google"}


async def test_never_requests_more_than_googles_per_call_maximum():
    """`num` above 10 is rejected by the API — and asking for more pages
    would mean more billable requests."""
    captured = {}

    async def capture_get(self, url, params=None, **kwargs):
        captured.update(params or {})
        return _FakeResponse({"items": []})

    with patch.object(httpx.AsyncClient, "get", capture_get):
        await google_client.search("query", limit=50)
    assert captured["num"] <= 10


async def test_is_available_requires_both_key_and_cx(monkeypatch):
    assert await google_client.is_available() is True
    monkeypatch.setattr(settings, "google_search_cx", "")
    assert await google_client.is_available() is False
