"""Unit tests for the web search provider cascade (app.search.web).

The whole point of the cascade is: if a provider errors or comes back
empty, the next one is tried automatically, in order, without the caller
ever seeing a broken request. These tests assert that behavior directly by
mocking each of the six provider clients — no network, no real API keys.

duckduckgo is the odd one out: it needs no key, so it's always
"configured" and is the thing standing between "auto" and a fully empty
response — most tests here explicitly stub it out (returning []) so they
can assert on the OTHER five without duckduckgo silently absorbing a
gap in the setup.
"""

from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.search import (
    duckduckgo_client,
    exa_client,
    google_client,
    linkup_client,
    serpapi_client,
    tavily_client,
    web,
)


@pytest.fixture(autouse=True)
def clean_provider_state(monkeypatch):
    """No keyed providers configured, auto mode, duckduckgo stubbed to []
    by default so it can't silently mask a gap in an individual test's
    setup — each test opts back into whatever it needs."""
    monkeypatch.setattr(settings, "web_search_provider", "auto")
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(settings, "tavily_api_key", "")
    monkeypatch.setattr(settings, "linkup_api_key", "")
    monkeypatch.setattr(settings, "exa_api_key", "")
    monkeypatch.setattr(settings, "serpapi_api_key", "")
    monkeypatch.setattr(duckduckgo_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(duckduckgo_client, "search_images", AsyncMock(return_value=[]))
    yield


def _configure_all(monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", "g-key")
    monkeypatch.setattr(settings, "tavily_api_key", "t-key")
    monkeypatch.setattr(settings, "linkup_api_key", "l-key")
    monkeypatch.setattr(settings, "exa_api_key", "e-key")
    monkeypatch.setattr(settings, "serpapi_api_key", "s-key")


def _hit(title: str) -> dict:
    return {"title": title, "url": f"https://example.com/{title}", "snippet": "s", "score": None}


async def test_search_returns_empty_when_every_provider_including_duckduckgo_fails():
    # duckduckgo already stubbed to [] by the autouse fixture; no keyed
    # provider configured either.
    assert await web.search("query") == []


async def test_search_falls_through_to_duckduckgo_as_the_true_last_resort(monkeypatch):
    """With zero API keys configured, duckduckgo is the only provider in
    the cascade at all — this is the whole point of it needing no key."""
    ddg_mock = AsyncMock(return_value=[_hit("ddg")])
    monkeypatch.setattr(duckduckgo_client, "search", ddg_mock)

    results = await web.search("query")

    assert [r["title"] for r in results] == ["ddg"]


async def test_search_uses_first_configured_provider(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(google_client, "search", AsyncMock(return_value=[_hit("g")]))
    tavily_mock = AsyncMock(return_value=[_hit("t")])
    monkeypatch.setattr(tavily_client, "search", tavily_mock)

    results = await web.search("query")

    assert [r["title"] for r in results] == ["g"]
    tavily_mock.assert_not_called()


async def test_search_falls_through_on_exception(monkeypatch):
    """The whole point: google blowing up must not break the request — tavily
    should answer instead, transparently to the caller."""
    _configure_all(monkeypatch)
    monkeypatch.setattr(google_client, "search", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(tavily_client, "search", AsyncMock(return_value=[_hit("t")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["t"]


async def test_search_falls_through_on_empty_result(monkeypatch):
    """A provider returning [] (quota exhausted, no hits, etc.) must cascade
    just like an exception does — [] is this codebase's uniform "didn't
    work" signal (see every client's own docstring)."""
    _configure_all(monkeypatch)
    monkeypatch.setattr(google_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(tavily_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[_hit("l")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["l"]


async def test_search_cascades_through_all_six_in_order(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(google_client, "search", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(tavily_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(exa_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(serpapi_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(duckduckgo_client, "search", AsyncMock(return_value=[_hit("ddg")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["ddg"]


async def test_search_returns_empty_when_every_provider_fails(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(google_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(tavily_client, "search", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(exa_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(serpapi_client, "search", AsyncMock(return_value=[]))
    # duckduckgo already stubbed to [] by the autouse fixture.

    assert await web.search("query") == []


async def test_unconfigured_providers_are_never_called(monkeypatch):
    """Only tavily has a key — google and serpapi must not even be
    attempted (no wasted call to a provider with no credentials).
    duckduckgo, needing none, IS still eligible but never reached since
    tavily answers first."""
    monkeypatch.setattr(settings, "tavily_api_key", "t-key")
    google_mock = AsyncMock(return_value=[_hit("g")])
    serpapi_mock = AsyncMock(return_value=[_hit("s")])
    monkeypatch.setattr(google_client, "search", google_mock)
    monkeypatch.setattr(serpapi_client, "search", serpapi_mock)
    monkeypatch.setattr(tavily_client, "search", AsyncMock(return_value=[_hit("t")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["t"]
    google_mock.assert_not_called()
    serpapi_mock.assert_not_called()


async def test_pinned_provider_does_not_fall_through(monkeypatch):
    """A pin means exactly that provider, full stop — no silent fallback to
    a different one (not even duckduckgo), which would defeat the point of
    pinning for debugging."""
    _configure_all(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "google")
    monkeypatch.setattr(google_client, "search", AsyncMock(side_effect=RuntimeError("boom")))
    tavily_mock = AsyncMock(return_value=[_hit("t")])
    monkeypatch.setattr(tavily_client, "search", tavily_mock)

    results = await web.search("query")

    assert results == []
    tavily_mock.assert_not_called()


async def test_pinned_to_duckduckgo_works_standalone(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "duckduckgo")
    monkeypatch.setattr(duckduckgo_client, "search", AsyncMock(return_value=[_hit("ddg")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["ddg"]


async def test_none_disables_search_entirely(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "none")
    google_mock = AsyncMock(return_value=[_hit("g")])
    monkeypatch.setattr(google_client, "search", google_mock)

    assert await web.search("query") == []
    google_mock.assert_not_called()


def test_is_configured_true_even_with_no_api_keys(monkeypatch):
    """duckduckgo needs no key — "auto" always has at least one provider to
    try, so is_configured() is only False on an explicit "none" pin."""
    assert web.is_configured() is True


def test_is_configured_false_when_explicitly_none(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "none")
    assert web.is_configured() is False


def test_active_provider_is_first_configured_in_cascade_order(monkeypatch):
    monkeypatch.setattr(settings, "linkup_api_key", "l-key")
    monkeypatch.setattr(settings, "exa_api_key", "e-key")
    # tavily comes before linkup/exa in cascade order even though it was
    # configured "later" here — order is fixed by priority, not by setup order.
    monkeypatch.setattr(settings, "tavily_api_key", "t-key")

    assert web.active_provider() == "tavily"


def test_active_provider_is_duckduckgo_when_nothing_else_configured():
    assert web.active_provider() == "duckduckgo"


def test_active_provider_none_when_explicitly_disabled(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "none")
    assert web.active_provider() == "none"


def test_configured_providers_lists_all_in_cascade_order(monkeypatch):
    monkeypatch.setattr(settings, "exa_api_key", "e-key")
    monkeypatch.setattr(settings, "google_api_key", "g-key")

    assert web.configured_providers() == ["google", "exa", "duckduckgo"]


async def test_is_available_true_if_any_provider_available(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(google_client, "is_available", AsyncMock(return_value=False))
    monkeypatch.setattr(tavily_client, "is_available", AsyncMock(return_value=True))

    assert await web.is_available() is True


async def test_search_images_falls_through_like_search(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(google_client, "search_images", AsyncMock(return_value=[]))
    monkeypatch.setattr(tavily_client, "search_images", AsyncMock(return_value=[]))
    monkeypatch.setattr(linkup_client, "search_images", AsyncMock(
        return_value=[{"img_url": "https://example.com/i.png", "thumbnail": "", "title": "", "source_url": "", "source_engine": "linkup"}]
    ))

    results = await web.search_images("query")

    assert len(results) == 1
    assert results[0]["source_engine"] == "linkup"


async def test_exa_search_images_is_always_empty_and_falls_through():
    """Exa has no image-search endpoint at all (see exa_client.py) — this
    must behave exactly like any other provider returning [], not raise."""
    from app.search import exa_client as real_exa_client
    assert await real_exa_client.search_images("query") == []
