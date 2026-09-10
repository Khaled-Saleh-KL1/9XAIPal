# Ingestion queue capacity check races

**Severity:** Medium  
**Confidence:** Confirmed from transaction flow  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Job creation now takes a PostgreSQL advisory transaction lock, counts unfinished jobs, and inserts the reservation before the transaction commits.

## What happens

Concurrent uploads or URL imports can push the number of unfinished ingestion
jobs above max_queued_ingestion_jobs even though every caller first passes the
capacity check.

## Why it happens

backend/app/services/ingestion.py:18-40 runs SELECT COUNT(*) and compares the
result in application code. Job creation is a separate INSERT in
create_ingestion_job. Callers perform file I/O and document commits between
those operations.

Two transactions can therefore read the same count below the limit and later
both insert jobs. Ordinary PostgreSQL isolation does not serialize this
predicate.

## Root cause

The queue limit is a shared invariant enforced with a non-locking
check-then-act sequence instead of an atomic reservation.

## Impact

A burst can exceed the disk/DB backlog intended to protect the single Celery
worker. Larger bursts can exceed it by many jobs.

## Suggested fix

Reserve queue capacity atomically before accepting the document. Options
include a locked singleton counter row updated in the same transaction, a
PostgreSQL advisory lock around count-and-insert, or a Redis Lua reservation
with rollback on failure. Keep the reservation through document and job
creation, and test simultaneous requests at limit minus one.
