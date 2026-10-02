# Workers Design

## Purpose

The `workers` directory contains the Celery task definitions and thin helpers for background
PDF ingestion (MinerU + chunking + assets) and embedding generation.

## Active Files

### `tasks.py`

Contains the real Celery `@celery_app.task` definitions:
- `process_ingestion`: full MinerU extraction pipeline (sync DB session)
- `embed_document`: batch embedding for a document; accepts `force=True` for a
  safe full re-embedding pass that replaces vectors by chunk id without first
  deleting the existing vector table. `scripts/reembed_library.py` queues this
  for every embedded document with chunks.
- `generate_section_summaries`: high-quality hierarchical section + paper-level summarization (runs after embeddings; can take many minutes; quality-first personal feature)

These are what the API actually calls via `.delay()`.

### `ingestion_worker.py`

Thin async wrapper around the pipeline (primarily used by tests and as documentation of the old design).

## Historical Note

The original design used FastAPI BackgroundTasks + an in-memory asyncio.Queue + `runner.py` /
`embedding_worker.py`. This was replaced by Celery + Redis for better isolation and durability.

## Data Dependencies

`workers` depends on `extraction`, `embeddings`, `services`, and `database` (both async and sync session layers).


## Celery queue split

`celery_worker` consumes only `ingest` (`process_ingestion` and
`reconstruct_reading_order`). `celery_worker_light` uses the same worker image,
environment and storage, and consumes the light/default queue named `celery`
(article imports, embeddings, section summaries and figure descriptions). The
original `celery` name is retained so queued messages survive deployment.
Both Compose files assign `WORKER_ROLE=ingest` / `WORKER_ROLE=light`.
A heavy task delivered to light uses Celery `Task.replace` on `ingest` before
any database, disk or extraction work. The original task id, callbacks,
errbacks, chains and chord membership follow the replacement; no placeholder
success result runs continuations early. Publication can be duplicated after
an ambiguous broker reply. There is no Redis forwarding receipt to retain.

Heavy consumers claim Postgres rows before work: ingestion by job id,
reading-order reconstruction by document id. Execution state is separate from
pipeline progress. A token and a 600-second lease are renewed every 60 seconds
by a heartbeat thread, including during long OCR calls. A session advisory lock
also fences live owners across lease expiry. An independent watchdog terminates
a worker if renewal stalls for 120 seconds, well before its lease expires. Duplicate deliveries are logged,
traced and acknowledged without changing status or running continuations.
Heavy outcomes are checkpointed durably before returning to Celery; terminal
claims are committed in `after_return`, after canvas publication/result storage.
A crash in between replays the saved result or error without re-extraction,
then resumes callbacks/chains/chords/errbacks. Continuation publication remains
at least once across ambiguous broker replies; exactly-once Redis publication
is not claimed.
Completed ingestion jobs and completed reading-order delivery ids remain
suppressed; a fresh reading-order request may execute after the previous one.
A crashed owner's restored delivery is requeued until lease expiry, then
reclaims the job. Database admission/finalization failures retain the delivery;
Heavy and article tasks reject late-ack work on prefork child loss;
loss of heartbeat fencing terminates the worker process so it cannot keep
writing without ownership. Late-ack recovery resumes the job.

URL source deliveries hold a document advisory lock across fetch, adoption
and failure handling. A concurrent delivery waits, then rechecks adoption, so
its delayed HTML response or fetch failure cannot clean up an adopted PDF.
PDF URL imports commit adoption and publish the same document/job ids to
`ingest`. Redelivery reuses the committed PDF before fetching or changing
status. Concurrent adoption locks the document row and preserves existing
bytes. An ambiguous publication requeues the article instead of failing an
already-running ingest job. HTML article processing stays on light.

`LIGHT_WORKER_CONCURRENCY` defaults to `2`; `LIGHT_WORKER_MEM_LIMIT` defaults to
`2G`. `WORKER_MEM_LIMIT` still caps the ingest worker (Compose defaults: `7G`
production, `12G` development). Production ingest concurrency stays `2`;
development keeps Celery's existing automatic concurrency. Backend/both deploys
build and update `api`, `celery_worker` and `celery_worker_light` together.

Startup recovery restores only messages for the restarting worker's queue;
only an ingest-role worker sweeps extraction scratch. Light never extracts
PDFs, including legacy or restored deliveries.

Rollback runs `scripts/rollback-celery-queues.sh` from the new tree before
restoring the old revision. After quiescing publishers and consumers it moves
queued priority buckets and reserved ingest messages atomically to `celery`,
rewriting exchange/routing metadata to `celery`/`celery`. Task ids, bodies,
headers, priorities and FIFO order within each bucket survive. Restore and
retry under the pre-split worker therefore stay on `celery`. The ingest binding
is removed only after migration. Redis errors abort rollback.
