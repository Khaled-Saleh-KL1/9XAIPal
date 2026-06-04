# Workers Design

## Purpose

The `workers` directory contains the Celery task definitions and thin helpers for background
PDF ingestion (MinerU + chunking + assets) and embedding generation.

## Active Files

### `tasks.py`

Contains the real Celery `@celery_app.task` definitions:
- `process_ingestion` — full MinerU extraction pipeline (sync DB session)
- `embed_document` — batch embedding for a document
- `generate_section_summaries` — high-quality hierarchical section + paper-level summarization (runs after embeddings; can take many minutes; quality-first personal feature)

These are what the API actually calls via `.delay()`.

### `ingestion_worker.py`

Thin async wrapper around the pipeline (primarily used by tests and as documentation of the old design).

## Historical Note

The original design used FastAPI BackgroundTasks + an in-memory asyncio.Queue + `runner.py` /
`embedding_worker.py`. This was replaced by Celery + Redis for better isolation and durability.

## Data Dependencies

`workers` depends on `extraction`, `embeddings`, `services`, and `database` (both async and sync session layers).

