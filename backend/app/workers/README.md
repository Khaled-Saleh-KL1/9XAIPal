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
original `celery` name is retained so queued messages survive deployment; old
PDF jobs in that backlog temporarily run on the light worker.

`LIGHT_WORKER_CONCURRENCY` defaults to `2`; `LIGHT_WORKER_MEM_LIMIT` defaults to
`2G`. `WORKER_MEM_LIMIT` still caps the ingest worker (Compose defaults: `7G`
production, `12G` development). Production ingest concurrency stays `2`;
development keeps Celery's existing automatic concurrency. Backend/both deploys
build and update `api`, `celery_worker` and `celery_worker_light` together.

Startup recovery restores only messages for the restarting worker's queue;
only the ingest worker sweeps extraction scratch directories. During migration,
an old PDF running from `celery` still shares scratch storage with ingest, so
ingest-worker restarts can affect that legacy extraction.
