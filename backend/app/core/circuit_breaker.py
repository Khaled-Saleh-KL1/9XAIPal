"""A tiny circuit breaker for provider cascades.

Both cascades in this app (web search — app/search/web.py, chat — app/llm/
client.py) try providers in a fixed priority order and fall through on
failure. That works, but it re-pays for a *known-dead* provider on every
single request: a provider that is down, out of quota, or geo-blocked fails
identically thousands of times in a row, each time costing a network
round-trip and an ERROR log line.

The concrete case this was built for: GOOGLE_API_KEY is a valid Gemini key
whose Search-grounding quota is effectively zero from an EEA-hosted server
(Google's free tier excludes the EEA). It sits first in the web search
cascade — where it belongs, so it takes over the moment billing is enabled —
and failed 100% of calls, costing ~0.13s of every search plus an ERROR line.

So: after ``FAILURE_THRESHOLD`` consecutive failures a provider is skipped
for ``COOLDOWN_SECONDS``, then gets one trial call. Success at any point
resets it completely. The provider stays FIRST in the configured order the
whole time — this only changes whether it is *called*, never its priority —
so a provider that starts working again is picked up automatically with no
config change.

⚠ Two deliberate limits:

* **State is per process.** Each uvicorn worker and each Celery worker keeps
  its own counters. That is fine: this is a latency/noise optimization, not a
  correctness mechanism, and per-process state means a restart always gives
  every provider a clean chance.
* **It never turns a working cascade into an empty one.** If every candidate
  is open, the caller is told to ignore the breaker and try them all anyway
  (see :func:`filter_open`) — a slow answer always beats no answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)

# Three strikes: enough that a single blip or one-off 500 never trips it,
# few enough that a genuinely dead provider stops costing anything quickly.
FAILURE_THRESHOLD = 3

# How long a tripped provider is skipped before one trial call is allowed.
# Five minutes keeps recovery prompt (a quota reset or a billing change is
# picked up within minutes) without retrying a hard-down provider constantly.
COOLDOWN_SECONDS = 300.0


@dataclass
class _State:
    consecutive_failures: int = 0
    opened_at: float | None = None


_states: dict[str, _State] = {}


def _state(name: str) -> _State:
    if name not in _states:
        _states[name] = _State()
    return _states[name]


def is_open(name: str, *, now: float | None = None) -> bool:
    """Whether ``name`` is currently being skipped.

    Returns False once the cooldown has elapsed, letting exactly one trial
    call through — ``record_failure`` re-opens it if that trial also fails.
    """
    st = _state(name)
    if st.opened_at is None:
        return False
    elapsed = (now if now is not None else time.monotonic()) - st.opened_at
    if elapsed >= COOLDOWN_SECONDS:
        # Half-open: let one call through to find out if it recovered.
        st.opened_at = None
        return False
    return True


def record_success(name: str) -> None:
    """Clear all failure state — a provider that answers is fully healthy."""
    st = _state(name)
    if st.consecutive_failures or st.opened_at is not None:
        logger.info("circuit breaker: %s recovered, closing", name)
    st.consecutive_failures = 0
    st.opened_at = None


def record_failure(name: str, *, now: float | None = None) -> None:
    """Count a failure; trip the breaker at FAILURE_THRESHOLD in a row."""
    st = _state(name)
    st.consecutive_failures += 1
    if st.consecutive_failures >= FAILURE_THRESHOLD:
        st.opened_at = now if now is not None else time.monotonic()
        logger.warning(
            "circuit breaker: %s failed %d times in a row, skipping it for %.0fs",
            name, st.consecutive_failures, COOLDOWN_SECONDS,
        )


def filter_open(names: list[str]) -> list[str]:
    """Drop currently-open providers from ``names``, preserving order.

    ⚠ If that would empty the list, the ORIGINAL list is returned instead:
    when everything is tripped, trying every provider and probably failing
    is still better than skipping straight to "no answer" without trying.
    """
    live = [n for n in names if not is_open(n)]
    if not live:
        return names
    return live


def reset(name: str | None = None) -> None:
    """Clear breaker state — one provider, or all of them (tests, reconfigure)."""
    if name is None:
        _states.clear()
    else:
        _states.pop(name, None)


def snapshot() -> dict[str, dict]:
    """Current state per provider, for /health and debugging."""
    return {
        name: {
            "consecutive_failures": st.consecutive_failures,
            "open": is_open(name),
        }
        for name, st in _states.items()
    }
