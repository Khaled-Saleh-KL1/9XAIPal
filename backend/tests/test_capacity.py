"""Unit tests for app.core.capacity: the concurrent-active-user cap and its
FIFO waiting queue.

Uses the real Redis instance (same as test_auth_http.py) rather than a mock:
the module is a handful of ZADD/ZCARD/ZREMRANGEBYSCORE/RPUSH/LPOS calls whose
correctness is entirely about how they compose, which a mock would just
restate rather than verify.
"""

import time
from uuid import uuid4

import pytest

from app.core import capacity
from app.core.config import settings


@pytest.fixture(autouse=True)
async def _fresh_redis_client():
    """See test_auth_http.py — app.core.redis caches one client for the
    process lifetime, which breaks across pytest-asyncio's per-test event
    loops unless reset here."""
    import app.core.redis as redis_module
    redis_module._client = None
    r = redis_module.get_redis()
    await r.flushdb()
    yield
    await r.flushdb()
    await r.aclose()
    redis_module._client = None


@pytest.fixture(autouse=True)
def _small_cap(monkeypatch):
    monkeypatch.setattr(settings, "max_active_users", 2)
    monkeypatch.setattr(settings, "active_window_seconds", 300)


def _uid():
    return uuid4()


async def test_first_users_up_to_the_cap_are_admitted_immediately():
    a, b = _uid(), _uid()
    admitted_a, pos_a = await capacity.touch_and_check_admission(a)
    admitted_b, pos_b = await capacity.touch_and_check_admission(b)
    assert (admitted_a, pos_a) == (True, None)
    assert (admitted_b, pos_b) == (True, None)
    assert await capacity.active_count() == 2


async def test_user_past_the_cap_is_queued_not_admitted():
    a, b, c = _uid(), _uid(), _uid()
    await capacity.touch_and_check_admission(a)
    await capacity.touch_and_check_admission(b)
    admitted, position = await capacity.touch_and_check_admission(c)
    assert admitted is False
    assert position == 1
    assert await capacity.queue_length() == 1


async def test_queue_position_is_fifo():
    a, b = _uid(), _uid()
    c, d = _uid(), _uid()
    await capacity.touch_and_check_admission(a)
    await capacity.touch_and_check_admission(b)
    _, pos_c = await capacity.touch_and_check_admission(c)
    _, pos_d = await capacity.touch_and_check_admission(d)
    assert (pos_c, pos_d) == (1, 2)


async def test_admission_is_sticky_a_newcomer_cannot_bump_an_active_user():
    a, b, c = _uid(), _uid(), _uid()
    await capacity.touch_and_check_admission(a)
    await capacity.touch_and_check_admission(b)
    await capacity.touch_and_check_admission(c)  # queued, cap is full

    # a re-checks in — still admitted, not evicted in favor of the queue.
    admitted, position = await capacity.touch_and_check_admission(a)
    assert (admitted, position) == (True, None)
    assert await capacity.active_count() == 2


async def test_repeated_poll_does_not_push_a_queued_user_to_the_back():
    a, b, c = _uid(), _uid(), _uid()
    await capacity.touch_and_check_admission(a)
    await capacity.touch_and_check_admission(b)
    _, first_position = await capacity.touch_and_check_admission(c)
    _, second_position = await capacity.touch_and_check_admission(c)
    assert first_position == second_position == 1


async def test_release_frees_a_slot_for_the_queue():
    a, b, c = _uid(), _uid(), _uid()
    await capacity.touch_and_check_admission(a)
    await capacity.touch_and_check_admission(b)
    admitted_c, _ = await capacity.touch_and_check_admission(c)
    assert admitted_c is False

    await capacity.release(a)
    assert await capacity.active_count() == 1

    admitted_c_now, position = await capacity.touch_and_check_admission(c)
    assert (admitted_c_now, position) == (True, None)
    assert await capacity.queue_length() == 0


async def test_idle_user_is_evicted_after_the_active_window(monkeypatch):
    a, b = _uid(), _uid()
    now = 1_000_000.0
    monkeypatch.setattr(capacity.time, "time", lambda: now)
    await capacity.touch_and_check_admission(a)

    # b arrives well past the window without a's ever touching again.
    now += settings.active_window_seconds + 1
    admitted_b, position = await capacity.touch_and_check_admission(b)
    assert (admitted_b, position) == (True, None)
    assert await capacity.active_count() == 1  # only b — a aged out


async def test_active_user_who_keeps_touching_never_ages_out(monkeypatch):
    """A single active user refreshed just inside every window boundary must
    never be evicted, no matter how much wall-clock time passes — and a
    challenger stays queued behind them the entire time."""
    monkeypatch.setattr(settings, "max_active_users", 1)
    a, c = _uid(), _uid()
    now = 1_000_000.0
    monkeypatch.setattr(capacity.time, "time", lambda: now)
    await capacity.touch_and_check_admission(a)

    for _ in range(5):
        now += settings.active_window_seconds - 1  # always inside the window
        admitted_a, _ = await capacity.touch_and_check_admission(a)
        assert admitted_a is True
        admitted_c, position = await capacity.touch_and_check_admission(c)
        assert admitted_c is False
        assert position == 1

    assert await capacity.active_count() == 1


async def test_exactly_at_the_cap_boundary():
    monkeypatch_users = [_uid() for _ in range(settings.max_active_users)]
    for uid in monkeypatch_users:
        admitted, _ = await capacity.touch_and_check_admission(uid)
        assert admitted is True
    assert await capacity.active_count() == settings.max_active_users

    one_more = _uid()
    admitted, position = await capacity.touch_and_check_admission(one_more)
    assert admitted is False
    assert position == 1
