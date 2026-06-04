# Test Plan

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
partial documents.

### `test_subthread_conversations.py`

Verifies sub-thread creation, history isolation, and thread-aware compaction.