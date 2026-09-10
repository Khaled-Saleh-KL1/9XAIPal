# Concurrent signup for one email returns an internal error

**Severity:** Medium  
**Confidence:** Confirmed check-then-insert race  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Signup catches the database unique-constraint race, rolls back the session, and returns the same generic 409 response as the pre-check.

## What happens

If two signup requests for the same email run concurrently, one can return an
unhandled database error instead of the endpoint's generic 409 response.

## Why it happens

backend/app/api/v1/endpoints/auth.py:50-65 first queries for the email, then
inserts and commits. Both requests can observe no existing row. The database's
unique index on LOWER(email) correctly rejects the second insert, but signup
does not catch sqlalchemy.exc.IntegrityError or roll back the session before
it leaves the endpoint.

## Root cause

Correctness relies on an application-level pre-check even though only the
database unique constraint can decide atomically. The expected constraint
failure is not translated into the documented API result.

## Impact

Normal double-submit, retries, or simultaneous signup attempts produce a 500,
an error log, and inconsistent user feedback. It can also expose a raw
database exception to generic error handling.

## Suggested fix

Keep the friendly pre-check, but treat the unique index as authoritative:
catch the specific IntegrityError around insert/commit, roll back, and return
the same generic 409. Avoid mapping unrelated integrity failures to that
response. Add a concurrency or mocked unique-violation test.
