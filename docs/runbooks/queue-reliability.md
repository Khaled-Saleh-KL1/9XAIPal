# Queue reliability operations

All commands below are operator actions after deployment. Tests use isolated local services and mock SMTP/HTTP.

## Durable failures

Every terminal Celery exception creates or updates `failed_jobs`. Celery RETRY and a later success create no failure row. Returned failed outcomes are captured too. Ingestion identity is task name plus ingestion job ID; document-related auxiliary tasks use task name plus document ID; maintenance tasks use delivery ID. A retried ingestion reuses its job with an incremented execution generation. Errors are scrubbed and byte-limited (2 KiB message, 8 KiB traceback). Diagnostics contain identifiers and exception information, never task argument payloads or file contents.

Input defects (bad/encrypted PDFs, zero pages/text, unsupported types, size limit, HTTP 4xx except transient 408/429) receive specific document messages. They appear in the summary without immediate mail. System and stalled failures enqueue an alert ID on the light `celery` queue. SMTP uses STARTTLS and the standard library. Empty host or sender/recipient disables sending and logs that details are available in the DLQ. A Redis Lua gate atomically admits the first fingerprint, suppresses repeat-window matches and enforces the daily cap until the next 08:00 summary boundary. Email bodies include a retry command and scrubbed diagnostics. Summary counts come from timestamped failure events rather than lifetime row attempts.

From the backend directory (or container):

```sh
python scripts/dlq.py list --open
python scripts/dlq.py show FAILURE_UUID
python scripts/dlq.py retry FAILURE_UUID
python scripts/dlq.py resolve FAILURE_UUID
```

Retry uses normal ingestion admission, owner capacity, existing files, and job generation fencing. An execution still holding its PDF/article advisory lock cannot be retried. Deleted documents must be resolved. Document-related auxiliary tasks can be re-enqueued by their existing Celery task; maintenance tasks without a document have no automatic retry payload and must be resolved after fixing the cause. Resolve acknowledges a failure without changing document state.

## Long jobs and stopped workers

Beat sweeps every five minutes. Job status/progress writers stamp `progress_updated_at`. MinerU page batches and Arabic page callbacks report real progress. A minute observer stamps work only when Linux main-thread CPU, owned subprocess CPU or output lengths move during a large blocking batch. PDF claim renewal maintains its execution lease and does not count as processing progress. Starting work stamps a heartbeat immediately; worker inspection is refreshed after the broker snapshot to protect newly dequeued successors. The observer for PDF, article, embedding and summarization tasks is pinned to the job and execution generation captured at task start; it never invents a progress fraction. On non-Linux systems, explicit status/page/batch callbacks remain the progress source.

A job is eligible only after `STALLED_JOB_MINUTES` without a heartbeat and while its document and latest job are in progress. Active/reserved Celery reports protect it for three thresholds. At three thresholds, even a reported active task can be marked failed. No sweeper kills a running task. Unknown or inconsistent inspection replies cause the sweep to skip. Jobs still in the queued stage are excluded. The sweeper separately scans the local Redis broker for waiting ingestion/embedding/summary messages; a pending successor protects the document, and an unavailable or incomplete broker snapshot skips the sweep. Active successor tasks are associated with their document, even when their delivery ID differs from the parent ingestion task. Check worker health and broker connectivity if inspection remains unavailable.

The sole light worker runs embedded beat with `-B --schedule /tmp/celerybeat-schedule`. Keep **exactly one replica** in both production and local compose. A daily summary runs around 08:00 Asia/Amman, only if there are period counts or open failures, and Redis prevents duplicate summaries after beat restarts. The summary is independent of the immediate-email cap. SMTP failures stay in the DLQ and do not enqueue another alert, preventing recursive mail failures. Diagnose them and resolve after fixing SMTP configuration; maintenance tasks have no document retry payload.

## Upload admission and identity

The ASGI admission guard checks authenticated ownership, global/per-user job depth, disk headroom and both session Redis and the configured broker before multipart parsing or original-file storage. The job insert repeats capacity checks under the existing PostgreSQL transaction lock. Responses use `queue_full` / `user_queue_full` (429), `storage_full` / `service_unavailable` (503), with `Retry-After` bounded between 30 and 600 seconds. The global queue delay estimate is 30 seconds per active job, capped at ten minutes. A free-space threshold supplements the existing disk-percent protection. The upload UI presents a per-code notice, disables retry during the countdown, and reuses the selected file's key on automatic retries.

Uploads read at most 1 MiB at a time and compute SHA-256 in that same loop. The multipart envelope is also bounded before disk spooling; the exact file-byte cap remains enforced in the endpoint. Size violations return 413 and remove the partial original. Content identity is scoped by user, with a partial database unique index and a transaction advisory lock around the decision, row creation and normal job reservation. A duplicate returns HTTP 200 and `duplicate: true`; losing temporary files are deleted. A failed document reuses both its document and latest failed job. The frontend offers the existing document and does not put a duplicate existing document into the new-upload deletion reference.

URLs normalize scheme/host/default port and remove fragments; query/path semantics remain intact. Credential-bearing URLs are rejected. New URL identities use their own partial unique index. The optional `Idempotency-Key` is recorded in a database ledger and cached with Redis SET NX EX 86400; the same key with a different identity returns 422. The database ledger preserves correctness during cache publication failures. Identity and replay are user-scoped; different users remain independent. Existing legacy documents have null identities until opt-in backfill, so the guarantee for historical unhashed files begins after backfill.

## Optional historical hash backfill

This script never runs during migration/startup. It hashes originals in 1 MiB reads, skips absent originals, reports same-owner historical duplicates and leaves them for manual review. It neither deletes nor merges documents.

```sh
python scripts/backfill_content_hash.py --dry-run
python scripts/backfill_content_hash.py --apply
```

## Post-deploy check

1. Confirm the light worker has exactly one replica and beat logs show both schedules.
2. In the backend environment, run `python scripts/dlq.py list --open` and `python scripts/backfill_content_hash.py --dry-run`.
3. Upload a small valid PDF twice as a test user. Verify first response 201, second 200 with the same ID and `duplicate: true`, and one ingestion job. Delete the test document through the normal library UI.
4. Verify job heartbeat timestamps move during processing. Use mocked SMTP in tests; do not manufacture production errors or call providers merely to test owner email.
