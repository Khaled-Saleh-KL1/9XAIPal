# API Design

## Purpose

The `api` directory owns the HTTP boundary of the backend. Routers should stay thin and delegate business logic to `services`, `chat`, `extraction`, and `database`.

## Files

### `deps.py`

Provides FastAPI dependencies for configuration, database access, request-scoped services, and shared validation helpers.

### `errors.py`

Maps domain exceptions to HTTP responses. Expected error families include missing documents, missing chunks, invalid cursors, failed extraction, unavailable models, and unavailable search.

### `v1/router.py`

Combines all versioned endpoint routers under `/api/v1`.

### `v1/endpoints/health.py`

Owns health endpoints for the API, database, local models, and optional external services.

### `v1/endpoints/documents.py`

Owns document upload, document listing, document detail, and deletion routes. Delegates lifecycle work to `services.documents` and ingestion startup to `services.ingestion`.

### `v1/endpoints/chunks.py`

Owns sequential reading endpoints. It should expose current, next, previous, and windowed chunk retrieval without performing raw SQL itself.

### `v1/endpoints/ask.py`

Owns the `/ask` HTTP endpoint. It validates request data and delegates routing, retrieval, model calls, and citation formatting to `chat.orchestrator`.

### `v1/endpoints/search.py`

Optional debugging and developer-facing endpoint group for local vector search, sequential lookup, and external SearXNG search.

## Data Dependencies

`api` imports `schemas` for request and response models, `services` for document and retrieval workflows, `chat` for `/ask`, and `core` for app settings.

`api` must not directly call MinerU, Ollama, SearXNG, raw PostgreSQL SQL, or pgvector queries.
