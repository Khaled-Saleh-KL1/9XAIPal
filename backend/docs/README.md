# 9XAIPal Backend

9XAIPal is a local-first FastAPI backend for structural PDF ingestion, sequential document reading, pgvector-backed RAG, and local multimodal LLM routing.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the implementation plan, directory tree, Dockerized PostgreSQL/pgvector outline, and folder-level documentation map.

## Core Principles

- PostgreSQL + pgvector runs locally (Docker Compose recommended).
- Physical document order is preserved with `document_id + sequence_id` (never overwritten by vectors).
- MinerU (magic-pdf) owns high-quality PDF structure extraction.
- Background work (ingestion + embeddings) runs in Celery workers (Redis).
- Ollama owns local model inference (chat + VLM + embeddings).
- SearXNG is the only external search path.
