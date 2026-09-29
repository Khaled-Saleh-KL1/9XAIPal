"""Semantic Scholar 429s are "slow down", not "the service is down".

Measured on production (2026-09-29): with the key valid and every call going
through the shared 1-request line, 9 of 12 lookups at 1.05 s spacing — and
still half at 3 s spacing — came back 429. Each exhausted lookup counted as a
circuit-breaker failure, so three of them opened the breaker and every
reference lookup failed instantly for five minutes: the feature looked dead.
A 429 must back off and retry, and must never open the outage breaker.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.core import circuit_breaker, pacer
from app.core.config import settings
from app.search import semantic_scholar_client as s2

_HIT = {"data": [{
    "title": "Attention Is All You Need",
    "authors": [{"name": "A. Vaswani"}], "year": 2017,
    "externalIds": {"ArXiv": "1706.03762"},
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
}]}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "semantic_scholar_api_key", "k")
    monkeypatch.setattr(settings, "semantic_scholar_min_interval_seconds", 1.0)
    monkeypatch.setattr(pacer, "wait_turn", AsyncMock(return_value=pacer.Turn(0.0, 0)))
    yield
    circuit_breaker.reset()


@pytest.fixture
def slept(monkeypatch):
    """Every backoff sleep the client takes, in seconds (none actually waited)."""
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(s2.asyncio, "sleep", fake_sleep)
    return waits


def _respond(monkeypatch, responses):
    """Serve `responses` (httpx.Response factories) in order, repeating the last."""
    fired: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fired.append(request)
        return responses[min(len(fired), len(responses)) - 1]()

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return fired


def _too_many(headers=None):
    return lambda: httpx.Response(429, json={"message": "Too Many Requests."}, headers=headers or {})


def _ok():
    return lambda: httpx.Response(200, json=_HIT)


def test_default_attempts_allow_for_a_heavily_throttled_key():
    assert type(settings).model_fields["semantic_scholar_max_attempts"].default == 6


@pytest.mark.asyncio
async def test_exhausted_429s_never_open_the_breaker(monkeypatch, slept):
    monkeypatch.setattr(settings, "semantic_scholar_max_attempts", 2)
    _respond(monkeypatch, [_too_many()])

    for _ in range(circuit_breaker.FAILURE_THRESHOLD + 2):
        assert await s2.match_reference("Vaswani et al. 2017") is s2.Unresolved.UNAVAILABLE

    assert not circuit_breaker.is_open(s2._BREAKER_ID)


@pytest.mark.asyncio
async def test_lookup_after_a_throttled_burst_still_goes_out(monkeypatch, slept):
    monkeypatch.setattr(settings, "semantic_scholar_max_attempts", 1)
    fired = _respond(monkeypatch, [_too_many(), _too_many(), _too_many(), _too_many(), _ok()])
    for _ in range(4):
        await s2.match_reference("Vaswani et al. 2017")

    result = await s2.match_reference("Vaswani et al. 2017")

    assert isinstance(result, s2.ReferenceMatch)
    assert len(fired) == 5


@pytest.mark.asyncio
async def test_429_backs_off_exponentially_then_resolves(monkeypatch, slept):
    monkeypatch.setattr(settings, "semantic_scholar_max_attempts", 6)
    _respond(monkeypatch, [_too_many(), _too_many(), _too_many(), _ok()])

    result = await s2.match_reference("Vaswani et al. 2017")

    assert isinstance(result, s2.ReferenceMatch)
    assert slept == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_backoff_is_capped(monkeypatch, slept):
    monkeypatch.setattr(settings, "semantic_scholar_max_attempts", 6)
    _respond(monkeypatch, [_too_many()])

    await s2.match_reference("Vaswani et al. 2017")

    assert slept == [1.0, 2.0, 4.0, 8.0, 8.0]


@pytest.mark.asyncio
async def test_retry_after_header_is_honoured(monkeypatch, slept):
    monkeypatch.setattr(settings, "semantic_scholar_max_attempts", 3)
    _respond(monkeypatch, [_too_many({"Retry-After": "3"}), _ok()])

    assert isinstance(await s2.match_reference("Vaswani et al. 2017"), s2.ReferenceMatch)
    assert slept == [3.0]


@pytest.mark.asyncio
async def test_unparseable_retry_after_uses_the_backoff_and_a_huge_one_is_capped(monkeypatch, slept):
    monkeypatch.setattr(settings, "semantic_scholar_max_attempts", 3)
    _respond(monkeypatch, [
        _too_many({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        _too_many({"Retry-After": "3600"}),
        _ok(),
    ])

    assert isinstance(await s2.match_reference("Vaswani et al. 2017"), s2.ReferenceMatch)
    assert slept == [1.0, 8.0]


@pytest.mark.asyncio
async def test_server_errors_still_open_the_breaker(monkeypatch, slept):
    _respond(monkeypatch, [lambda: httpx.Response(503, json={"message": "down"})])

    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        assert await s2.match_reference("Vaswani et al. 2017") is s2.Unresolved.UNAVAILABLE

    assert circuit_breaker.is_open(s2._BREAKER_ID)


@pytest.mark.asyncio
async def test_first_try_success_does_not_sleep(monkeypatch, slept):
    _respond(monkeypatch, [_ok()])

    assert isinstance(await s2.match_reference("Vaswani et al. 2017"), s2.ReferenceMatch)
    assert slept == []
