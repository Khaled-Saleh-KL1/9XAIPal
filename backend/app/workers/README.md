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
a worker if renewal stalls for 120 seconds, well before its lease expires. Live-owner deliveries are rejected/requeued so a restored reservation remains recoverable
if its owner later dies. Finished delivery ids are logged, traced and acknowledged without
re-extraction. Distinct ingestion delivery ids replay the saved outcome for their own canvas.
Heavy tasks disable Celery's pre-body `STARTED` result write so a dropped
failed duplicate cannot overwrite the original failure; Postgres still reports
pipeline progress. Other task settings and retry budgets remain unchanged.
Heavy outcomes are checkpointed durably before returning to Celery; terminal
claims are committed in `after_return`, after canvas publication/result storage.
A crash in between replays the saved result or error without re-extraction,
then resumes callbacks/chains/chords/errbacks. Continuation publication remains
at least once across ambiguous broker replies; exactly-once Redis publication
is not claimed.
Every physical Redis receipt gets a fresh reservation tag before acknowledgment
bookkeeping. Restored or lost-reply copies retain their logical task/canvas IDs,
but a stale owner’s ACK cannot erase a newer reservation. Admission rejects
release their DB locks and pause 250 ms before requeueing, avoiding a hot loop
through new DB/TCP connections while an unfinished owner or lease is unchanged.
Finished ingestion delivery ids remain suppressed within a retry generation. Deliberate
Arabic confirmation advances the generation and clears ownership/outcome transactionally;
old-generation deliveries cannot execute the confirmed job. A distinct ingestion source
replays the saved result/error without heavy work. Completed reading-order delivery ids
remain suppressed; a fresh reading-order request is retained until the unfinished
prior delivery has executed or replayed its checkpoint and completed Celery
finalization. Pending retries also retain the original delivery’s ownership.
The newer request then executes without replacing the saved outcome.
A crashed owner's restored delivery is requeued until lease expiry, then
reclaims the job. Database admission/finalization failures retain the delivery;
Heavy and article tasks reject late-ack work on prefork child loss;
loss of heartbeat fencing terminates the worker process so it cannot keep
writing without ownership. Subprocesses spawned inside the heavy claim run through
Linux supervisors which observe the pool child with a pidfd, adopt orphaned
descendants as subreapers, and kill/reap the entire extraction tree on owner death.
A separate shared Postgres descendants lock prevents recovery until reaping
finishes, even after the original lease expires. The rollback overlay carries
the same supervisor. Late-ack recovery resumes the job after both fences permit it.

URL source deliveries hold a document advisory lock on the same pinned connection used
for every persistence statement across fetch, adoption and failure handling. SQL verifies
that physical connection still owns the lock; connection loss rejects/requeues the source
without failure cleanup or an HTML persistence tail. A concurrent delivery waits, then rechecks adoption, so
its delayed HTML response or fetch failure cannot clean up an adopted PDF.
PDF URL tasks commit adoption and use native `Task.replace` to transfer their id and
callbacks/errbacks/chains/chords to `process_ingestion` on `ingest`. The source cannot
complete its canvas before extraction. Direct pipeline callers publish the same document/job ids. Redelivery reuses the committed PDF before fetching or changing
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

Rollback to a pre-split revision keeps a compatibility consumer: the workflow saves the
current claim/forwarding/compatibility modules outside the rsync target, restores the old
tree, then runs `scripts/prepare-celery-rollback.py` before rebuilding. The old worker
consumes `celery` with current Postgres fencing, outcome replay and retry generations;
its original extraction code stays in use. Adopted PDF source copies reuse the committed
file and replace onto `celery`. Queued and reserved duplicates remain safe, including
copies with different source ids/canvases and already-finished jobs. The API's deliberate
confirmation retry also retains generation advancement. Overlay failure aborts rollback
before any consumer starts. This is a compatibility rollback, not a byte-identical old tree.

URL PDF storage stages complete bytes privately, then serializes publication and
row adoption with a stable filesystem lock plus a checked Postgres row lock.
Committed PDFs remain immutable. When the row still describes an article,
canonical/raw files are uncommitted residue and both are replaced atomically
with the freshly fetched complete bytes, including truncated files left by
pre-split writers. The filesystem lock survives DB connection loss until the
stale publisher stops, so a successor cannot commit adoption before publication
is fenced. Raw storage uses the same bytes as the canonical PDF. Article failure
cleanup/status writes commit together; ownership/persistence failure requeues
the source instead of acknowledging the original fetch error. Abandoned private
stage files can remain after a crash; they are never admitted as canonical PDFs.

Compatibility rollback carries queue-only schema DDL into API migrations and checks it
before worker startup, even if the failed deployment never reached migrations. Schema
failure stops the worker. Queue-aware rollback revisions retain their separate roles,
current guards and native article handoff without wrapping guards twice. Early split
revisions also receive queue-scoped recovery and explicit roles. English/PDF extraction
functions are retained from the restored revision; only the URL adoption/handoff phase
is refreshed for queue-aware revisions.
