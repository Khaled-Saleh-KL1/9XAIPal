"""Unit tests for app.search.google_client's response parsing.

Gemini's Search-grounding tool has hit 429 RESOURCE_EXHAUSTED on every live
call made against it so far (see docs/decisions.md, 2026-08-31) — the
parsing logic below was never exercised by a real successful response
before shipping. These tests substitute for that: a mocked response
matching Gemini's documented groundingMetadata shape, verifying the
extraction is correct, plus every malformed/empty variant a real API could
plausibly return, verifying each degrades to `[]` rather than raising.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import settings
from app.search import google_client


@pytest.fixture(autouse=True)
def has_key(monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", "test-key")


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _mock_post(data):
    async def fake_post(*args, **kwargs):
        return _FakeResponse(data)
    return fake_post


async def test_extracts_grounding_chunks_with_cited_text_as_snippet():
    """The documented shape: groundingChunks (the sources) each mapped to
    the answer segment(s) that cited them, used as the snippet since
    grounding gives no meta-description the way a SERP would."""
    data = {
        "candidates": [{
            "groundingMetadata": {
                "groundingChunks": [
                    {"web": {"uri": "https://arxiv.org/abs/2307.08691", "title": "FlashAttention-2 - arXiv"}},
                    {"web": {"uri": "https://openreview.net/forum?id=x", "title": "FlashAttention-2 - OpenReview"}},
                ],
                "groundingSupports": [
                    {
                        "segment": {"text": "FlashAttention-2 is a fast attention algorithm."},
                        "groundingChunkIndices": [0, 1],
                    },
                ],
            },
        }],
    }
    with patch.object(httpx.AsyncClient, "post", _mock_post(data)):
        results = await google_client.search("query")

    assert len(results) == 2
    assert results[0]["url"] == "https://arxiv.org/abs/2307.08691"
    assert results[0]["title"] == "FlashAttention-2 - arXiv"
    assert results[0]["snippet"] == "FlashAttention-2 is a fast attention algorithm."
    assert results[1]["url"] == "https://openreview.net/forum?id=x"


async def test_chunk_with_no_citing_segment_gets_empty_snippet_not_crash():
    data = {
        "candidates": [{
            "groundingMetadata": {
                "groundingChunks": [{"web": {"uri": "https://example.com", "title": "T"}}],
                "groundingSupports": [],
            },
        }],
    }
    with patch.object(httpx.AsyncClient, "post", _mock_post(data)):
        results = await google_client.search("query")

    assert results == [{"title": "T", "url": "https://example.com", "snippet": "", "source_engine": "google", "score": None}]


@pytest.mark.parametrize("data,label", [
    ({}, "empty dict"),
    ({"candidates": []}, "no candidates"),
    ({"candidates": [{}]}, "candidate with no groundingMetadata key"),
    ({"candidates": [{"groundingMetadata": {}}]}, "no groundingChunks"),
    ({"candidates": [{"groundingMetadata": {"groundingChunks": []}}]}, "empty groundingChunks"),
    ({"candidates": [{"groundingMetadata": {"groundingChunks": [{"web": {}}]}}]}, "chunk with no uri"),
    ({"candidates": [{"groundingMetadata": {"groundingChunks": [{}]}}]}, "chunk with no web key at all"),
])
async def test_malformed_or_empty_shapes_degrade_to_empty_list(data, label):
    with patch.object(httpx.AsyncClient, "post", _mock_post(data)):
        results = await google_client.search("query")
    assert results == [], label


async def test_no_key_returns_empty_without_network_call(monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", "")
    post_mock = AsyncMock()
    with patch.object(httpx.AsyncClient, "post", post_mock):
        results = await google_client.search("query")
    assert results == []
    post_mock.assert_not_called()


async def test_http_error_degrades_to_empty_list():
    async def raising_post(*args, **kwargs):
        raise httpx.HTTPStatusError("429", request=None, response=httpx.Response(429))
    with patch.object(httpx.AsyncClient, "post", raising_post):
        results = await google_client.search("query")
    assert results == []


async def test_search_images_always_empty_no_network_call():
    """Search grounding is text-only — no image results, and no reason to
    even attempt a call."""
    post_mock = AsyncMock()
    with patch.object(httpx.AsyncClient, "post", post_mock):
        results = await google_client.search_images("query")
    assert results == []
    post_mock.assert_not_called()
