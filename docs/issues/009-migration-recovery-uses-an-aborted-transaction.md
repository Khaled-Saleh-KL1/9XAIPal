# Migration recovery uses an aborted transaction

**Severity:** High  
**Confidence:** Confirmed PostgreSQL transaction behavior  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Each recovery statement now runs in its own transaction, so a failed repair cannot abort later independent repairs.

## What happens

If any statement in _ensure_recent_columns fails, the intended recovery loop
cannot successfully execute later statements. Startup can remain on a partial
schema even though logs say each failed item was skipped.

## Why it happens

The main migration runner correctly gives each statement its own transaction
in backend/app/database/migrations.py:48-54. The recovery function does the
opposite at lines 250-257: it opens one engine.begin block for the entire list
and catches exceptions around individual execute calls.

PostgreSQL marks a transaction aborted after a statement error. Catching the
Python exception does not roll the transaction back. Every later execute then
fails with InFailedSQLTransaction, and leaving the context may fail its commit.
This is especially reachable on a damaged older schema because some ALTER
TABLE statements precede fallback CREATE TABLE statements.

## Root cause

Statement-level error handling is combined with a transaction whose failure
state spans all statements. No savepoint or per-statement transaction clears
that state.

## Impact

The safety net designed to repair partial migrations stops at the first
problem, potentially leaving missing columns/tables and preventing reliable
application startup.

## Suggested fix

Execute each recovery statement in its own engine.begin block, matching the
main runner, or wrap each execute in begin_nested/savepoint and roll it back
on failure. Reorder table creation before ALTER statements as defense in
depth. Test with an intentionally absent prerequisite and assert later
independent changes are still applied.
