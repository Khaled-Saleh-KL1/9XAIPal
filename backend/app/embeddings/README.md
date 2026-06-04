# Embeddings Design

## Purpose

The `embeddings` directory owns local embedding generation for global semantic retrieval.

Embeddings are stored in PostgreSQL through pgvector and must never become the source of physical document order. They support similarity search only.

## Files

### `model.py`

Wraps the local embedding backend, validates vector dimensions, batches embedding requests, and normalizes vectors if required.

### `service.py`

Generates embeddings for chunks, stores them through repository interfaces, rebuilds document embeddings, and detects missing vectors.

### `queue.py`

Provides a local queue abstraction for embedding jobs. This may begin in-process and later move to a separate worker.

## Data Dependencies

`embeddings` reads chunk text from `database.repositories.chunks` and stores vectors through `database.repositories.embeddings`, which delegates pgvector-specific operations to `database.pgvector`.

`chat.global_context` depends on stored embeddings for vector retrieval.
