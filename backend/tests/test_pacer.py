"""app.core.pacer — the shared 1-request-per-interval line in Redis that
every Semantic Scholar call waits in (search/semantic_scholar_client.py).

Real Redis, same as test_capacity.py: the whole point of the module is one
atomic Lua script whose correctness is about ordering under concurrency —
a mock would only restate the intent.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core import pacer
from app.core.config import settings


@pytest.fixture(autouse=True)
async def _fresh_redis_client():
    import app.core.redis as redis_module
    redis_module._client = None
    r = redis_module.get_redis()
    await r.flushdb()
    yield
    await r.flushdb()


@pytest.mark.asyncio
async def test_first_caller_goes_straight_through():
    turn = await pacer.take_turn("t", 0.5)
    assert turn.wait_seconds == 0
    assert turn.position == 0


@pytest.mark.asyncio
async def test_simultaneous_callers_are_lined_up_one_interval_apart():
    """Ten callers arriving at once get ten distinct slots, 0, 1, 2 … intervals
    out, with positions to match — nobody shares a slot, nobody is skipped."""
    turns = await asyncio.gather(*(pacer.take_turn("t", 0.2) for _ in range(10)))
    waits = sorted(t.wait_seconds for t in turns)
    for i, w in enumerate(waits):
        assert abs(w - i * 0.2) < 0.05, (i, w)
    assert sorted(t.position for t in turns) == list(range(10))


@pytest.mark.asyncio
async def test_lines_are_independent():
    await pacer.take_turn("a", 1.0)
    turn = await pacer.take_turn("b", 1.0)
    assert turn.wait_seconds == 0


@pytest.mark.asyncio
async def test_wait_turn_actually_spaces_the_calls():
    """Three callers through wait_turn fire at least one interval apart, in
    arrival order. This is the property Semantic Scholar's limit needs."""
    fired: list[tuple[str, float]] = []

    async def caller(name: str):
        await pacer.wait_turn("t", 0.25)
        fired.append((name, time.monotonic()))

    async with asyncio.TaskGroup() as tg:
        for name in ("first", "second", "third"):
            tg.create_task(caller(name))
            await asyncio.sleep(0.01)  # distinct arrival order

    assert [n for n, _ in fired] == ["first", "second", "third"]
    gaps = [b - a for (_, a), (_, b) in zip(fired, fired[1:])]
    assert all(g >= 0.2 for g in gaps), gaps


@pytest.mark.asyncio
async def test_on_queued_is_told_the_place_only_when_there_is_a_wait():
    told: list[pacer.Turn] = []

    async def on_queued(turn: pacer.Turn):
        told.append(turn)

    await pacer.wait_turn("t", 0.2, on_queued=on_queued)      # empty line: silent
    assert told == []
    await pacer.take_turn("t", 0.2)                            # someone else reserves the next slot
    await pacer.wait_turn("t", 0.2, on_queued=on_queued)      # now we are third in
    assert len(told) == 1
    assert told[0].position == 2
    assert 0.3 < told[0].wait_seconds <= 0.4


@pytest.mark.asyncio
async def test_redis_outage_degrades_to_no_pacing(monkeypatch):
    import app.core.redis as redis_module

    class Broken:
        async def eval(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(redis_module, "_client", Broken())
    turn = await pacer.take_turn("t", 1.0)
    assert turn == pacer.Turn(0.0, 0)


def _s2_transport(monkeypatch):
    """Route the client's httpx.AsyncClient at a canned Semantic Scholar hit."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "k"
        return httpx.Response(200, json={"data": [{
            "title": "Attention Is All You Need",
            "authors": [{"name": "A. Vaswani"}], "year": 2017,
            "externalIds": {"ArXiv": "1706.03762"},
            "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
        }]})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.asyncio
async def test_semantic_scholar_client_never_burns_a_slot_without_a_key(monkeypatch):
    from app.search import semantic_scholar_client as s2

    monkeypatch.setattr(settings, "semantic_scholar_api_key", "")
    taken = AsyncMock(return_value=pacer.Turn(0.0, 0))
    monkeypatch.setattr(pacer, "wait_turn", taken)
    assert await s2.match_reference("Vaswani et al. 2017") is s2.Unresolved.UNAVAILABLE
    taken.assert_not_awaited()


@pytest.mark.asyncio
async def test_semantic_scholar_client_waits_its_turn_and_reports_it(monkeypatch):
    """match_reference takes a slot in the shared line before the HTTP call
    and relays the queued callback with the caller's place."""
    from app.search import semantic_scholar_client as s2

    monkeypatch.setattr(settings, "semantic_scholar_api_key", "k")
    monkeypatch.setattr(settings, "semantic_scholar_min_interval_seconds", 0.2)
    _s2_transport(monkeypatch)

    told: list[pacer.Turn] = []

    async def on_queued(turn: pacer.Turn):
        told.append(turn)

    # Someone else holds the next slot, so this call has to wait one interval.
    await pacer.take_turn(s2._PACER_LINE, 0.2)

    started = time.monotonic()
    result = await s2.match_reference("Vaswani et al. 2017", on_queued=on_queued)
    assert isinstance(result, s2.ReferenceMatch)
    assert result.arxiv_id == "1706.03762"
    assert told and told[0].position == 1
    assert time.monotonic() - started >= 0.15
