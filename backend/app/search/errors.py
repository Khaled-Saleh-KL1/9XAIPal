"""Search provider failure signalling.

Every provider client used to swallow its own errors and return ``[]``,
which made two different things indistinguishable to the cascade in
``web.py``:

* "I broke" — network error, auth failure, quota exhausted, bad response
  shape. The provider is unhealthy.
* "I worked, there are genuinely no hits for this query." The provider is
  perfectly healthy.

Both look like ``[]``. The cascade treats both the same way (fall through to
the next provider, which is right either way), but the circuit breaker must
not: tripping a provider because someone searched for a nonsense string
would skip a healthy provider on every later query, and — the case that
motivated this — never tripping on a real failure means a permanently-dead
provider keeps costing a round-trip on every single request forever.

So clients raise ``ProviderError`` for the first case and return ``[]`` only
for the second. ``web.py`` is the only consumer of the client modules (the
"one door" rule), and it catches this, so raising never escapes to a caller.
"""


class ProviderError(Exception):
    """A search provider failed — as opposed to returning no results."""
