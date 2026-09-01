"""Unit tests for the web search provider cascade (app.search.web).

The whole point of the cascade is: if a provider errors or comes back
empty, the next one is tried automatically, in order, without the caller
ever seeing a broken request. These tests assert that behavior directly by
mocking each of the five provider clients — no network, no real API keys.

duckduckgo is the odd one out: it needs no key, so it's always
"configured" and is the thing standing between "auto" and a fully empty
response — most tests here explicitly stub it out (returning []) so they
can assert on the OTHER five without duckduckgo silently absorbing a
gap in the setup.
"""

from unittest.mock import AsyncMock

import pytest

from app.core import circuit_breaker
from app.core.config import settings
from app.search import (
    duckduckgo_client,
    exa_client,
    linkup_client,
    serpapi_client,
    tavily_client,
    web,
)
from app.search.errors import ProviderError


@pytest.fixture(autouse=True)
def clean_provider_state(monkeypatch):
    """No keyed providers configured, auto mode, duckduckgo stubbed to []
    by default so it can't silently mask a gap in an individual test's
    setup — each test opts back into whatever it needs.

    ⚠ The circuit breaker keeps module-level state, so it MUST be reset
    around every test: a test that trips a provider would otherwise have it
    skipped in the next test, and the failure would look like a cascade bug.
    """
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "web_search_provider", "auto")
    monkeypatch.setattr(settings, "tavily_api_key", "")
    monkeypatch.setattr(settings, "linkup_api_key", "")
    monkeypatch.setattr(settings, "exa_api_key", "")
    monkeypatch.setattr(settings, "serpapi_api_key", "")
    monkeypatch.setattr(duckduckgo_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(duckduckgo_client, "search_images", AsyncMock(return_value=[]))
    yield
    circuit_breaker.reset()


def _configure_all(monkeypatch):
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
    monkeypatch.setattr(tavily_client, "search", AsyncMock(return_value=[_hit("t")]))
    linkup_mock = AsyncMock(return_value=[_hit("l")])
    monkeypatch.setattr(linkup_client, "search", linkup_mock)

    results = await web.search("query")

    assert [r["title"] for r in results] == ["t"]
    linkup_mock.assert_not_called()


async def test_search_falls_through_on_exception(monkeypatch):
    """The whole point: tavily blowing up must not break the request — linkup
    should answer instead, transparently to the caller."""
    _configure_all(monkeypatch)
    monkeypatch.setattr(tavily_client, "search", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[_hit("l")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["l"]


async def test_search_falls_through_on_empty_result(monkeypatch):
    """A provider returning [] (quota exhausted, no hits, etc.) must cascade
    just like an exception does — [] is this codebase's uniform "didn't
    work" signal (see every client's own docstring)."""
    _configure_all(monkeypatch)
    monkeypatch.setattr(tavily_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(exa_client, "search", AsyncMock(return_value=[_hit("e")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["e"]


async def test_search_cascades_through_all_five_in_order(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(tavily_client, "search", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(exa_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(serpapi_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(duckduckgo_client, "search", AsyncMock(return_value=[_hit("ddg")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["ddg"]


async def test_search_returns_empty_when_every_provider_fails(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(tavily_client, "search", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(exa_client, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(serpapi_client, "search", AsyncMock(return_value=[]))
    # duckduckgo already stubbed to [] by the autouse fixture.

    assert await web.search("query") == []


async def test_unconfigured_providers_are_never_called(monkeypatch):
    """Only tavily has a key — linkup and serpapi must not even be
    attempted (no wasted call to a provider with no credentials).
    duckduckgo, needing none, IS still eligible but never reached since
    tavily answers first."""
    monkeypatch.setattr(settings, "tavily_api_key", "t-key")
    linkup_mock = AsyncMock(return_value=[_hit("l")])
    serpapi_mock = AsyncMock(return_value=[_hit("s")])
    monkeypatch.setattr(linkup_client, "search", linkup_mock)
    monkeypatch.setattr(serpapi_client, "search", serpapi_mock)
    monkeypatch.setattr(tavily_client, "search", AsyncMock(return_value=[_hit("t")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["t"]
    linkup_mock.assert_not_called()
    serpapi_mock.assert_not_called()


async def test_pinned_provider_does_not_fall_through(monkeypatch):
    """A pin means exactly that provider, full stop — no silent fallback to
    a different one (not even duckduckgo), which would defeat the point of
    pinning for debugging."""
    _configure_all(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "tavily")
    monkeypatch.setattr(tavily_client, "search", AsyncMock(side_effect=RuntimeError("boom")))
    linkup_mock = AsyncMock(return_value=[_hit("l")])
    monkeypatch.setattr(linkup_client, "search", linkup_mock)

    results = await web.search("query")

    assert results == []
    linkup_mock.assert_not_called()


async def test_pinned_to_duckduckgo_works_standalone(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "duckduckgo")
    monkeypatch.setattr(duckduckgo_client, "search", AsyncMock(return_value=[_hit("ddg")]))

    results = await web.search("query")

    assert [r["title"] for r in results] == ["ddg"]


async def test_none_disables_search_entirely(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "none")
    tavily_mock = AsyncMock(return_value=[_hit("t")])
    monkeypatch.setattr(tavily_client, "search", tavily_mock)

    assert await web.search("query") == []
    tavily_mock.assert_not_called()


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
    monkeypatch.setattr(settings, "tavily_api_key", "t-key")

    assert web.configured_providers() == ["tavily", "exa", "duckduckgo"]


async def test_is_available_true_if_any_provider_available(monkeypatch):
    _configure_all(monkeypatch)
    monkeypatch.setattr(tavily_client, "is_available", AsyncMock(return_value=False))
    monkeypatch.setattr(tavily_client, "is_available", AsyncMock(return_value=True))

    assert await web.is_available() is True


async def test_search_images_falls_through_like_search(monkeypatch):
    _configure_all(monkeypatch)
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


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker: a repeatedly-failing provider stops being called, while a
# provider that merely finds nothing keeps its place. The distinction is the
# whole reason clients raise ProviderError instead of returning []
# (app/search/errors.py).
# ─────────────────────────────────────────────────────────────────────────────

async def test_repeated_provider_errors_trip_the_breaker_and_stop_the_calls(monkeypatch):
    """A permanently-broken first provider must stop being called at all
    after FAILURE_THRESHOLD failures, so it stops costing a round-trip on
    every later search."""
    _configure_all(monkeypatch)
    tavily_mock = AsyncMock(side_effect=ProviderError("tavily (429: quota exhausted)"))
    monkeypatch.setattr(tavily_client, "search", tavily_mock)
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[_hit("l")]))

    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        assert [r["title"] for r in await web.search("query")] == ["l"]

    calls_before = tavily_mock.await_count
    # Breaker is now open: further searches must skip tavily entirely.
    assert [r["title"] for r in await web.search("query")] == ["l"]
    assert tavily_mock.await_count == calls_before, "tavily was called while tripped"
    assert circuit_breaker.is_open("tavily") is True


async def test_empty_results_never_trip_the_breaker(monkeypatch):
    """A provider answering "no hits" for an obscure query is healthy.
    Tripping it would skip a working provider on every later search."""
    _configure_all(monkeypatch)
    tavily_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(tavily_client, "search", tavily_mock)
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[_hit("l")]))

    for _ in range(circuit_breaker.FAILURE_THRESHOLD + 2):
        await web.search("query")

    assert circuit_breaker.is_open("tavily") is False
    assert tavily_mock.await_count == circuit_breaker.FAILURE_THRESHOLD + 2


async def test_a_success_after_failures_keeps_the_provider_in_rotation(monkeypatch):
    _configure_all(monkeypatch)
    calls = {"n": 0}

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ProviderError("tavily (transient 500)")
        return [_hit("t")]

    monkeypatch.setattr(tavily_client, "search", flaky)
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[_hit("l")]))

    await web.search("query")   # fail 1 -> linkup
    await web.search("query")   # fail 2 -> linkup
    assert [r["title"] for r in await web.search("query")] == ["t"]  # recovers
    assert circuit_breaker.is_open("tavily") is False

    # The streak was reset by that success, so one more failure must not trip it.
    calls["n"] = 0
    await web.search("query")
    assert circuit_breaker.is_open("tavily") is False


async def test_a_pin_bypasses_the_breaker(monkeypatch):
    """Pinning exists to watch one provider, including watching it fail —
    the breaker must not silently skip it and return nothing instead."""
    _configure_all(monkeypatch)
    monkeypatch.setattr(settings, "web_search_provider", "tavily")
    tavily_mock = AsyncMock(side_effect=ProviderError("tavily (429)"))
    monkeypatch.setattr(tavily_client, "search", tavily_mock)

    for _ in range(circuit_breaker.FAILURE_THRESHOLD + 2):
        assert await web.search("query") == []

    assert tavily_mock.await_count == circuit_breaker.FAILURE_THRESHOLD + 2


async def test_cascade_still_answers_when_every_provider_is_tripped(monkeypatch):
    """filter_open's safety valve, end to end: with everything tripped the
    cascade must still try (and here, still succeed), not skip to empty."""
    _configure_all(monkeypatch)
    for name in ["tavily", "linkup", "exa", "serpapi", "duckduckgo"]:
        for _ in range(circuit_breaker.FAILURE_THRESHOLD):
            circuit_breaker.record_failure(name)

    monkeypatch.setattr(tavily_client, "search", AsyncMock(side_effect=ProviderError("still down")))
    monkeypatch.setattr(linkup_client, "search", AsyncMock(return_value=[_hit("l")]))

    assert [r["title"] for r in await web.search("query")] == ["l"]
