"""Unit tests for app.search.quota — the hard daily cap on metered search.

The requirement these encode: Google's Custom Search bills past 100
queries/day, and it must be impossible to exceed that by accident. Every
test here is about a way the cap could leak.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.config import settings
from app.search import quota


class _FakeRedis:
    """Minimal INCR/EXPIRE/GET, shared between "processes" in a test."""

    def __init__(self):
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.incr_calls = 0

    async def incr(self, key):
        self.incr_calls += 1
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        self.expires[key] = ttl

    async def get(self, key):
        value = self.store.get(key)
        return str(value) if value is not None else None


class _BrokenRedis:
    async def incr(self, key):
        raise ConnectionError("redis is down")

    async def get(self, key):
        raise ConnectionError("redis is down")


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(quota, "get_redis", lambda: r)
    return r


async def test_allows_calls_up_to_the_limit(fake_redis):
    for i in range(5):
        assert await quota.try_consume("google", 5) is True, f"call {i + 1} rejected early"


async def test_blocks_the_call_that_would_exceed(fake_redis):
    for _ in range(5):
        await quota.try_consume("google", 5)
    assert await quota.try_consume("google", 5) is False


async def test_stays_blocked_afterwards(fake_redis):
    for _ in range(10):
        await quota.try_consume("google", 3)
    assert await quota.try_consume("google", 3) is False
    assert await quota.try_consume("google", 3) is False


async def test_counter_is_shared_so_separate_workers_cannot_each_spend_the_limit(fake_redis):
    """The reason this lives in Redis: two uvicorn workers plus a Celery
    worker with in-memory counters would each allow a full 100."""
    # Three "processes" all reading the same Redis.
    allowed = 0
    for _ in range(12):
        if await quota.try_consume("google", 4):
            allowed += 1
    assert allowed == 4


async def test_reserves_before_the_call_so_a_failed_request_still_counts(fake_redis):
    """Counting before the request means a failure can only ever make us
    UNDER-use the quota. Refunding on failure could bill, since Google may
    well have counted the request itself."""
    assert await quota.try_consume("google", 1) is True
    assert fake_redis.store[list(fake_redis.store)[0]] == 1
    assert await quota.try_consume("google", 1) is False


async def test_fails_closed_when_redis_is_unreachable(monkeypatch):
    """An uncountable call is exactly the one that could cost money."""
    monkeypatch.setattr(quota, "get_redis", lambda: _BrokenRedis())
    assert await quota.try_consume("google", 100) is False


async def test_limit_of_zero_blocks_everything(fake_redis):
    """Set the limit to 0 to switch a provider off without unsetting keys —
    and it must not even touch Redis to decide that."""
    assert await quota.try_consume("google", 0) is False
    assert fake_redis.incr_calls == 0


async def test_quota_is_per_day_in_pacific_time(fake_redis):
    """Keyed to Google's own reset timezone. A UTC key would give a window
    each day where this counter reset but Google's had not."""
    pt = ZoneInfo("America/Los_Angeles")
    day1 = datetime(2026, 9, 1, 23, 0, tzinfo=pt)
    day2 = datetime(2026, 9, 2, 0, 30, tzinfo=pt)

    for _ in range(3):
        await quota.try_consume("google", 3, now=day1)
    assert await quota.try_consume("google", 3, now=day1) is False
    # New Pacific day → fresh allowance.
    assert await quota.try_consume("google", 3, now=day2) is True


async def test_same_pacific_day_across_a_utc_midnight_shares_one_budget(fake_redis):
    """23:00 PT and 01:00 UTC the next calendar day are the SAME Pacific
    day — a UTC-keyed counter would wrongly hand out a second allowance."""
    pt = ZoneInfo("America/Los_Angeles")
    before = datetime(2026, 9, 1, 23, 0, tzinfo=pt)
    after_utc_midnight = datetime(2026, 9, 2, 1, 0, tzinfo=ZoneInfo("UTC"))  # = 18:00 PT Sep 1

    for _ in range(2):
        await quota.try_consume("google", 2, now=before)
    assert await quota.try_consume("google", 2, now=after_utc_midnight) is False


async def test_providers_have_separate_budgets(fake_redis):
    await quota.try_consume("google", 1)
    assert await quota.try_consume("google", 1) is False
    assert await quota.try_consume("someone_else", 1) is True


async def test_used_today_reports_the_count(fake_redis):
    await quota.try_consume("google", 10)
    await quota.try_consume("google", 10)
    assert await quota.used_today("google") == 2


async def test_used_today_is_zero_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(quota, "get_redis", lambda: _BrokenRedis())
    assert await quota.used_today("google") == 0


async def test_default_limit_matches_googles_free_allowance():
    """If this ever drifts above 100, the deployment starts paying."""
    assert settings.google_search_daily_limit <= 100
