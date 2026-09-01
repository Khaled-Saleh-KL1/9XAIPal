"""Hard daily request caps for metered search providers.

Google's Custom Search JSON API gives 100 free queries per day and then
**bills** ($5 per 1000). A cascade that retries providers can burn through
100 queries quickly and silently, so this enforces a ceiling that cannot be
exceeded even by accident — the explicit requirement being "make sure it
does not exceed so I don't pay anything".

Three properties make that guarantee real:

* **Shared across processes.** The counter lives in Redis, not in memory:
  the API runs two uvicorn workers and the Celery worker searches too, so
  three independent in-memory counters would each happily allow the full
  100.
* **Reserve before calling.** The counter is incremented *before* the
  request goes out, and never decremented if the request fails. Miscounting
  in the direction of "used more than we did" can only ever keep us under
  the limit; the reverse could bill.
* **Fails closed.** If Redis is unreachable the provider is skipped rather
  than called, because an uncountable call is exactly the one that could
  cost money. The cascade just moves to the next provider, so a Redis blip
  degrades search quality slightly instead of risking a bill.

The window is keyed to **US/Pacific** dates because that is when Google's
own quota resets; keying on UTC would open a several-hour window each day
where this counter had reset but Google's had not.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

# Google's Custom Search quota resets at midnight Pacific Time.
_QUOTA_TZ = ZoneInfo("America/Los_Angeles")

# Keep a used-up counter around well past its day so a late call on a
# rolled-over clock can't wrap back onto a stale, low count.
_KEY_TTL_SECONDS = 60 * 60 * 48


def _key(provider: str, *, now: datetime | None = None) -> str:
    day = (now or datetime.now(_QUOTA_TZ)).astimezone(_QUOTA_TZ).strftime("%Y-%m-%d")
    return f"search_quota:{provider}:{day}"


async def try_consume(provider: str, limit: int, *, now: datetime | None = None) -> bool:
    """Reserve one request against today's quota.

    Returns True if the caller may make the request. Returns False when the
    limit is already used up, or when the count cannot be established at
    all — see the module docstring on why that also means "don't call".
    """
    if limit <= 0:
        return False
    key = _key(provider, now=now)
    try:
        redis = get_redis()
        used = await redis.incr(key)
        if used == 1:
            await redis.expire(key, _KEY_TTL_SECONDS)
    except Exception as e:
        logger.warning(
            "%s quota check failed (%s) — skipping the provider rather than "
            "risking an uncounted, billable request", provider, e,
        )
        return False

    if used > limit:
        # Log once per day's worth of rollover rather than on every attempt.
        if used == limit + 1:
            logger.warning(
                "%s daily free quota of %d is used up for today (US/Pacific); "
                "skipping it until the quota resets", provider, limit,
            )
        return False
    return True


async def used_today(provider: str, *, now: datetime | None = None) -> int:
    """Reservation attempts made today. 0 if unknown.

    ⚠ This counts *attempts*, so it keeps climbing past the limit as refused
    calls are still counted — the reservation is a single atomic INCR, which
    is what makes the cap race-free, and re-reading to avoid over-counting
    would reintroduce a check-then-act window. Requests above the limit
    never reach the provider, so a number here above the limit means
    "the cap did its job N times", not "N billable requests went out".
    """
    try:
        redis = get_redis()
        value = await redis.get(_key(provider, now=now))
        return int(value) if value else 0
    except Exception:
        return 0
