# Core Design

## Purpose

The `core` directory contains application-wide infrastructure: configuration, logging, filesystem path management, and lifecycle hooks.

## Files

### `config.py`

Defines typed settings loaded from environment variables. Important settings include the PostgreSQL connection URL, storage root, MinerU location, Ollama base URL, chat model, VLM model, embedding model, SearXNG URL, vector dimension, and upload limits.

### `logging.py`

Configures structured local logging. Logs should carry request IDs, document IDs, chunk IDs, ingestion job IDs, router decisions, selected model names, and latency.

### `lifecycle.py`

Owns FastAPI startup and shutdown behavior. Startup should ensure local folders exist, verify PostgreSQL connectivity, verify the pgvector extension, apply migrations, and verify optional service availability.

### `paths.py`

Centralizes runtime filesystem paths for uploaded PDFs, MinerU outputs, extracted images, and logs. PostgreSQL data lives in the Docker volume declared by `docker-compose.yml`.

## Data Dependencies

Most backend modules depend on `core`.

`core` should not depend on `api`, `chat`, `extraction`, or `services`, which keeps foundational infrastructure free from business logic.
