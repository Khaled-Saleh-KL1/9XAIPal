"""Concurrent-active-user cap, with a FIFO waiting queue for the overflow.

Signup is open (no invite code — see app.api.v1.endpoints.auth), and this is
a single box with one Celery worker, no autoscaling. This exists purely as a
defensive ceiling in case a burst of traffic ever shows up at once: admit up
to MAX_ACTIVE_USERS, queue everyone past that, and auto-promote from the
queue the moment a slot frees. Not expected to matter day to day — it should
be invisible at normal usage levels and only kick in under real load.

Reuses the same Redis instance and "touch on every authenticated request"
idiom app.core.auth already uses for session sliding-expiration — this is
not new infrastructure, just a second small piece of state alongside it.

⚠ **"Active" cannot mean "has a valid session".** Sessions live 30 days
(SESSION_TTL_SECONDS); using that as the presence signal would mean the cap
fills up permanently the 30th time anyone ever logs in and never frees
again. Active means "made an authenticated request recently" — a sliding
window, refreshed on every request, expired members swept off the front on
every check. No background job: the sweep happens inline, for free, on the
next request after someone goes idle.
"""

import time
from uuid import UUID

from app.core.config import settings
from app.core.redis import get_redis

_ACTIVE_KEY = "capacity:active_users"
_QUEUE_KEY = "capacity:waiting_queue"


async def touch_and_check_admission(user_id: UUID) -> tuple[bool, "int | None"]:
    """Refresh presence and report admission status for this user.

    Returns ``(admitted, queue_position)`` — ``queue_position`` is ``None``
    whenever ``admitted`` is True, and a 1-based position (their place in
    line, not counting themselves in the active count) when it's False.

    Admission is STICKY: once in, a user keeps their slot for as long as
    they stay active, regardless of how many others are waiting — a new
    arrival can never bump someone already using the site. This also means
    the "cap" is really "at most N *distinct* people active in any given
    ACTIVE_WINDOW_SECONDS window", not a hard ceiling on requests.
    """
    uid = str(user_id)
    r = get_redis()
    now = time.time()
    cutoff = now - settings.active_window_seconds

    # Sweep first: anyone who's gone quiet longer than the window no longer
    # holds a slot, whether or not this call is theirs.
    await r.zremrangebyscore(_ACTIVE_KEY, "-inf", cutoff)

    # Already in? Just refresh their timestamp — sticky admission.
    if await r.zscore(_ACTIVE_KEY, uid) is not None:
        await r.zadd(_ACTIVE_KEY, {uid: now})
        await r.lrem(_QUEUE_KEY, 0, uid)  # in case they were queued before
        return True, None

    active_count = await r.zcard(_ACTIVE_KEY)
    if active_count < settings.max_active_users:
        await r.zadd(_ACTIVE_KEY, {uid: now})
        await r.lrem(_QUEUE_KEY, 0, uid)
        return True, None

    # Not admitted: take (or keep) a place in the FIFO queue. LPOS finds an
    # existing entry so a repeated poll doesn't push someone to the back of
    # their own line.
    position = await r.lpos(_QUEUE_KEY, uid)
    if position is None:
        await r.rpush(_QUEUE_KEY, uid)
        position = await r.llen(_QUEUE_KEY) - 1
    return False, position + 1


async def release(user_id: UUID) -> None:
    """Free this user's slot immediately, rather than waiting out the idle
    window. Called from logout — no reason to make someone else wait out a
    5-minute timer when the seat is deliberately being given up right now.
    """
    uid = str(user_id)
    r = get_redis()
    await r.zrem(_ACTIVE_KEY, uid)
    await r.lrem(_QUEUE_KEY, 0, uid)


async def active_count() -> int:
    """How many distinct users currently hold a slot. For /health and tests."""
    r = get_redis()
    cutoff = time.time() - settings.active_window_seconds
    await r.zremrangebyscore(_ACTIVE_KEY, "-inf", cutoff)
    return await r.zcard(_ACTIVE_KEY)


async def queue_length() -> int:
    """How many distinct users are currently waiting. For /health and tests."""
    r = get_redis()
    return await r.llen(_QUEUE_KEY)
