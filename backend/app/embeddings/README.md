# Embeddings Design

## Purpose

The `embeddings` directory owns embedding generation for global semantic
retrieval. The backend (local Ollama or a cloud API) is chosen by
`app.llm.resolver.resolve_embedding()` — see `app/llm/README.md`. Only
Ollama, OpenAI, and Gemini can serve embeddings (Anthropic/xAI/DeepSeek have
no embedding APIs).

Embeddings are stored in PostgreSQL through pgvector and must never become
the source of physical document order. They support similarity search only.

Every stored vector carries the `embedding_model` that produced it. Vectors
from different models are NOT comparable: the resolver pins the auto-detected
choice per process, and startup (`core/lifecycle.py`) detects a stored-vs-
active mismatch — with a pinned `EMBEDDING_PROVIDER` it wipes stale vectors
and re-embeds the library automatically; in `auto` mode it only warns.

## Files

### `model.py`

Resolves the embedding target per batch, builds the provider-specific payload
(Ollama `/api/embed`; OpenAI/Gemini OpenAI-compatible `/embeddings` with a
`dimensions` hint), validates vector counts, and normalizes dimensions to
`VECTOR_DIMENSION` (truncate+renormalize larger outputs, zero-pad smaller).
Exposes `active_embedding_model()` / `active_embedding_model_sync()` so
callers can record which model produced a batch.

### `service.py`

Async service: generates embeddings for chunks, stores them through the
repository layer with the resolved model name, rebuilds document embeddings,
and detects missing vectors.

### `service_sync.py`

Sync mirror of `service.py` used by the Celery workers.

## Data Dependencies

`embeddings` depends on `app.llm.resolver` for backend/model selection, reads
chunk text from `database.repositories.chunks`, and stores vectors through
`database.repositories.embeddings`, which delegates pgvector-specific
operations to `database.pgvector`.

`chat.global_context` depends on stored embeddings for vector retrieval.
