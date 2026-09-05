"""_last_compaction_time_in_thread returns something comparable to a turn's
`created_at`.

The one caller counts user turns newer than this value:

    sum(1 for t in history if t["role"] == "user" and t["created_at"] > since)

`created_at` comes back from asyncpg as a datetime. The helper used to return
`.isoformat()` — a str — and `datetime > str` is a TypeError, not a wrong
number. It was raised inside the compaction task, whose caller logs and
swallows exceptions, so nothing surfaced: sub-thread compaction simply never
ran, and every tangent thread went on replaying its full history to the model,
getting slower and more expensive with each turn. Main-chat compaction was
unaffected (it counts in SQL), which is why this stayed hidden.
"""

from datetime import datetime, timedelta, timezone

from app.chat.orchestrator import _last_compaction_time_in_thread

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _turn(role: str, minutes: int) -> dict:
    return {"role": role, "created_at": T0 + timedelta(minutes=minutes)}


def test_the_cutoff_compares_against_a_real_turn_timestamp():
    """The actual regression: this expression must not raise."""
    history = [_turn("user", 0), _turn("assistant", 1), _turn("compaction", 2), _turn("user", 3)]
    since = _last_compaction_time_in_thread(history)
    assert sum(1 for t in history if t["role"] == "user" and t["created_at"] > since) == 1


def test_with_no_compaction_yet_every_turn_counts():
    history = [_turn("user", 0), _turn("assistant", 1), _turn("user", 5)]
    since = _last_compaction_time_in_thread(history)
    assert sum(1 for t in history if t["role"] == "user" and t["created_at"] > since) == 2


def test_the_latest_compaction_wins_not_the_first():
    """A thread compacted twice must count from the second one, or it
    re-compacts material it already summarised on every subsequent turn."""
    history = [
        _turn("compaction", 1),
        _turn("user", 2),
        _turn("compaction", 3),
        _turn("user", 4),
    ]
    assert _last_compaction_time_in_thread(history) == T0 + timedelta(minutes=3)


def test_the_empty_thread_cutoff_is_older_than_any_real_turn():
    assert _last_compaction_time_in_thread([]) < T0
