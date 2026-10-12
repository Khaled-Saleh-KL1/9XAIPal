# Core Design

## Purpose

The `core` directory contains application-wide infrastructure: configuration, logging, filesystem path management, and lifecycle hooks.

## Files

### `config.py`

Defines typed settings loaded from environment variables. Important settings include the PostgreSQL connection URL, storage root, MinerU location, Ollama base URL, chat model, VLM model, embedding model, web-search and article-scraping provider settings, optional Cloudflare image-generation settings, the contextual Arabic embedding flag, vector dimension, and upload limits.

The v3 defaults also enable Arabic document routing and automatic handwriting,
with separate local classifier, gateway, direct Gemini and Gemma settings. The
canonical defaults are in the [configuration reference](../../../docs/03-reference/configuration.md#arabic-document-routing-and-ocr).
Do not dump the full settings object into logs: it contains credentials.

### `logging.py`

Configures structured local logging. Logs should carry request IDs, document IDs, chunk IDs, ingestion job IDs, router decisions, selected model names, and latency.

### `lifecycle.py`

Owns FastAPI startup and shutdown behavior. Startup should ensure local folders exist, verify PostgreSQL connectivity, verify the pgvector extension, apply migrations, and verify optional service availability.

When Arabic OCR is enabled, lifecycle startup invokes the printed Flash capability
probe from `extraction/arabic_capability.py`. That current probe requires direct Gemini
keys, even with a gateway configured; it does not prove handwritten Pro access.

### `paths.py`

Centralizes runtime filesystem paths for uploaded PDFs, MinerU outputs, extracted images, and logs. PostgreSQL data lives in the Docker volume declared by `docker-compose.yml`.

## Data Dependencies

Most backend modules depend on `core`.

Foundational settings, paths and logging stay independent of HTTP/reader workflows.
`lifecycle.py` is a coordination boundary and imports extraction capability checks
for startup. `celery_app.py` owns queue and scheduled maintenance configuration;
the light worker handles availability refresh and queue reliability schedules.
