"""Unit tests for app.core.circuit_breaker.

The breaker exists so a permanently-dead provider (the case that motivated
it: a Gemini key whose grounding quota is zero from an EEA server, sitting
first in the web search cascade) stops costing a network round-trip and an
ERROR log on every single request — while keeping its priority, so it takes
over again the moment it recovers.
"""

import pytest

from app.core import circuit_breaker as cb


@pytest.fixture(autouse=True)
def clean_breaker():
    cb.reset()
    yield
    cb.reset()


def test_healthy_provider_is_never_open():
    assert cb.is_open("google") is False


def test_stays_closed_below_the_threshold():
    for _ in range(cb.FAILURE_THRESHOLD - 1):
        cb.record_failure("google")
    assert cb.is_open("google") is False


def test_opens_at_the_threshold():
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure("google")
    assert cb.is_open("google") is True


def test_a_success_resets_the_streak():
    """Two failures then a success must not leave the provider one failure
    away from tripping — an intermittent blip is not a dead provider."""
    cb.record_failure("google")
    cb.record_failure("google")
    cb.record_success("google")
    cb.record_failure("google")
    assert cb.is_open("google") is False


def test_success_closes_an_open_breaker():
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure("google")
    assert cb.is_open("google") is True
    cb.record_success("google")
    assert cb.is_open("google") is False


def test_cooldown_expiry_allows_one_trial_call():
    """Half-open: after the cooldown the provider is tried again, so a
    recovered provider comes back automatically with no config change."""
    now = 1000.0
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure("google", now=now)
    assert cb.is_open("google", now=now) is True
    assert cb.is_open("google", now=now + cb.COOLDOWN_SECONDS - 1) is True
    assert cb.is_open("google", now=now + cb.COOLDOWN_SECONDS + 1) is False


def test_failed_trial_call_reopens_the_breaker():
    now = 1000.0
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure("google", now=now)
    # Cooldown elapses, one trial call is allowed...
    assert cb.is_open("google", now=now + cb.COOLDOWN_SECONDS + 1) is False
    # ...and it fails too, so it goes straight back to open.
    cb.record_failure("google", now=now + cb.COOLDOWN_SECONDS + 2)
    assert cb.is_open("google", now=now + cb.COOLDOWN_SECONDS + 3) is True


def test_providers_are_tracked_independently():
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure("google")
    assert cb.is_open("google") is True
    assert cb.is_open("tavily") is False


def test_filter_open_drops_tripped_providers_preserving_order():
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure("google")
    names = ["google", "tavily", "linkup", "exa"]
    assert cb.filter_open(names) == ["tavily", "linkup", "exa"]


def test_filter_open_returns_everything_when_all_are_tripped():
    """The safety valve: skipping straight to "no answer" because every
    provider is tripped is worse than trying them and probably failing."""
    names = ["google", "tavily"]
    for name in names:
        for _ in range(cb.FAILURE_THRESHOLD):
            cb.record_failure(name)
    assert cb.filter_open(names) == names


def test_reset_clears_one_provider():
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure("google")
        cb.record_failure("tavily")
    cb.reset("google")
    assert cb.is_open("google") is False
    assert cb.is_open("tavily") is True


def test_snapshot_reports_state():
    cb.record_failure("google")
    snap = cb.snapshot()
    assert snap["google"]["consecutive_failures"] == 1
    assert snap["google"]["open"] is False
