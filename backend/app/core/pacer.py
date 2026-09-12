"""A shared turnstile for providers that allow N requests per interval.

Semantic Scholar's keyed tier is **one request per second, per key** — and
this API runs `--workers 2`, so a per-process limiter would let two workers
each think they are alone and fire two requests in the same second. The
line therefore lives in Redis, which every worker already shares for
sessions and capacity.

The mechanism is a single Lua script, so taking a turn is atomic: it reads
the timestamp the *next* request may fire at, pushes that timestamp forward
by one interval for whoever comes after, and hands the caller its own slot.
Arrival order is turn order — strictly FIFO, no polling, no lock held
across the wait. The caller then sleeps until its slot and fires. Redis's
own clock (`TIME`) is the reference so two workers with drifting clocks
still agree on the line; what the caller sleeps is the *delta* the script
computed, so its local clock never has to match Redis's.

⚠ A slot is reserved the moment it is taken, whether or not the caller
lives to use it (a client that closes the tab mid-wait). That costs one
interval of idle provider time, which is the right trade: the alternative
— releasing slots — needs a lock held across the sleep and reintroduces
the race this exists to remove.

Redis unreachable degrades to "no pacing" with a warning rather than to a
hard failure: a missing turnstile makes a 429 likelier (which the caller's
circuit breaker already handles), while an exception here would make every
citation lookup fail outright for an infrastructure blip that has nothing
to do with the lookup.
"""

import asyncio
import math
from typing import Awaitable, Callable, NamedTuple, Optional

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

_TAKE_TURN = """
local key = KEYS[1]
local interval_ms = tonumber(ARGV[1])
local t = redis.call('TIME')
local now_ms = t[1] * 1000 + math.floor(t[2] / 1000)
local next_at = tonumber(redis.call('GET', key) or '0')
if next_at < now_ms then
  next_at = now_ms
end
-- The key only needs to outlive the last reserved slot; a generous
-- expiry keeps an abandoned line from lingering forever in Redis.
redis.call('SET', key, next_at + interval_ms, 'PX', (next_at + interval_ms - now_ms) + 60000)
return {next_at - now_ms, now_ms}
"""


class Turn(NamedTuple):
    """The caller's place in the line: how long until its slot, and how
    many reserved slots are ahead of it (0 = fire now)."""
    wait_seconds: float
    position: int


async def take_turn(name: str, interval_seconds: float) -> Turn:
    """Reserve the next free slot in the `name` line. Never raises."""
    interval_ms = max(1, int(round(interval_seconds * 1000)))
    try:
        r = get_redis()
        wait_ms, _now = await r.eval(_TAKE_TURN, 1, f"pacer:{name}", interval_ms)
        wait_ms = max(0, int(wait_ms))
    except Exception as e:  # noqa: BLE001 — see module docstring
        logger.warning("pacer %s: Redis unavailable, not pacing (%s)", name, e)
        return Turn(0.0, 0)
    return Turn(wait_ms / 1000.0, math.ceil(wait_ms / interval_ms) if wait_ms else 0)


async def wait_turn(
    name: str,
    interval_seconds: float,
    on_queued: Optional[Callable[[Turn], Awaitable[None]]] = None,
) -> Turn:
    """Take a turn and sleep until it comes. `on_queued` is awaited once,
    before the sleep, only when there is actually a wait — so a caller that
    streams progress can tell the reader "you're #3, opens shortly" without
    saying anything for the common no-wait case."""
    turn = await take_turn(name, interval_seconds)
    if turn.wait_seconds > 0:
        if on_queued is not None:
            await on_queued(turn)
        await asyncio.sleep(turn.wait_seconds)
    return turn
