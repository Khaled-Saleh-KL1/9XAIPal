# Test Plan

## ⚠ How to run these

```bash
POSTGRES_DB=scholarflow_test pytest
```

**Every test truncates `documents CASCADE`.** That cascade takes chunks,
assets, conversations, and notes with it, so running the suite against the
development database deletes your entire library — the rows go, and only the
PDFs on disk survive. `conftest.py` refuses to start unless `POSTGRES_DB`
contains "test" (override with `ALLOW_DESTRUCTIVE_TESTS=1` if you genuinely
mean it).

First-time setup of the scratch database:

```bash
docker exec scholarflow-postgres psql -U scholarflow -d postgres -c 'CREATE DATABASE "scholarflow_test"'
docker exec scholarflow-postgres psql -U scholarflow -d scholarflow_test \
  -c 'CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
```

## Purpose

The `tests` directory validates architectural guarantees.

## Required Test Areas

### `test_chunk_sequence.py`

Verifies that sequence IDs are assigned in physical order and that sequential
chunk retrieval works.

### `test_vector_retrieval.py`

Verifies that embeddings are stored for chunks in PostgreSQL, pgvector search
returns chunk IDs, and retrieved chunks can be ordered by similarity.

### `test_context_router.py`

Verifies that LOCAL prompts route to LOCAL, document-wide prompts route to
GLOBAL, overview prompts route to OVERVIEW, and web-dependent prompts route to
EXTERNAL.

### `test_ingestion_pipeline.py`

Verifies that MinerU output becomes normalized chunks, images are attached
correctly, ingestion is transactional, and failed ingestion does not expose
partial documents. Also pins the profile split: a paper under
`INGEST_PROFILE=fast` is complete at chunking with nothing dispatched, while a
book still runs the full embed → summarize chain.

### `test_subthread_conversations.py`

Verifies sub-thread creation, history isolation, and thread-aware compaction.