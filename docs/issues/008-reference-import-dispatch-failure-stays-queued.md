# Failed reference imports remain queued forever

**Severity:** Medium  
**Confidence:** Confirmed from exception path  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Reference-import dispatch failures now persist failed document and job states in the same session before responding.

## What happens

When “add reference to library” creates its document and job but Celery
dispatch fails, the response says the import failed while the database keeps
both the document and ingestion job in their initial queued state.

## Why it happens

backend/app/api/v1/endpoints/chunks.py:675-700 commits the new document and
then the queued job. The dispatch exception handler at lines 701-708 returns
an AddReferenceResponse with status="failed", but unlike the normal URL import
handler it never calls update_document_status or update_job_status and never
commits a failed state.

## Root cause

The reference-specific import path duplicated dispatch logic but omitted the
failure-state persistence used by backend/app/api/v1/endpoints/documents.py.

## Impact

The library can show a permanently queued/processing paper. Each orphan job is
also counted by check_queue_capacity, so repeated broker failures can consume
all queue slots even though no task can process those rows.

## Suggested fix

Use one shared create-and-dispatch service for uploads, URL imports, and
reference imports. On dispatch failure, mark both records failed in one
transaction with a useful error message. Add a test with delay() raising and
assert the persisted job and document statuses.
