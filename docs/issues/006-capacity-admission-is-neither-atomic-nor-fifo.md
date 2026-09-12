# Capacity admission is neither atomic nor FIFO

**Severity:** High  
**Confidence:** Confirmed from Redis command ordering  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Admission, expiry, queue insertion, and promotion now run in one Redis Lua script; queue heartbeats remove abandoned heads without changing active users' order.

## What happens

Concurrent callers can both be admitted into the final available slot, making
the active-user count exceed max_active_users. When a slot opens, a newcomer
or a later queued poller can also take it before the first waiting user.

## Why it happens

backend/app/core/capacity.py:33-74 performs expiry cleanup, membership lookup,
active count, admission, and queue removal as separate Redis commands. Two
requests can both observe active_count below the cap before either executes
ZADD.

The admission branch checks only active_count. It never checks whether the
waiting list is non-empty or whether the caller is at its head. Consequently,
the code described as FIFO does not use queue order when choosing who gets a
free slot.

## Root cause

A multi-step compare-and-update operation was implemented as independent Redis
round trips, and queue position is used only for display rather than admission.

## Impact

The load-shedding guarantee fails during the burst it is intended to handle.
Waiting users can starve while later callers jump the queue.

## Suggested fix

Move pruning, active membership, queue insertion, head selection, and ZADD
into one Redis Lua script. Admit only an already-active user or the queue head
when capacity exists; when no queue exists, atomically admit the newcomer.
Add concurrent tests and a test where a newcomer polls before queue position
one after a release.
