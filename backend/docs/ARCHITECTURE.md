# 9XAIPal Backend Architecture

## Current Execution Model (as of 2026)

Long-running work is dispatched to **Celery workers** (Redis broker):
- `POST /papers/upload` writes files + DB rows, then calls `process_ingestion.delay()`.
- The Celery task (`workers/tasks.py`) runs the full MinerU + chunking + asset pipeline **synchronously** inside the worker.
- Embeddings are also dispatched to Celery (`embed_document.delay`).
- After embeddings complete, `generate_section_summaries.delay()` fires automatically.
- The old in-process `BackgroundTasks` + `asyncio.Queue` design has been replaced by Celery.

See `docker-compose.yml` (postgres + redis + searxng + celery_worker) and `app/core/celery_app.py`.

## Docker Compose

`docker-compose.yml` lives at the backend root:

| Service         | Image | Port | Purpose |
| --------------- | ----- | ---- | ------- |
| `postgres`      | `pgvector/pgvector:pg16` | 5432 | Database with pgvector |
| `redis`         | `redis:7-alpine` | 6379 | Celery broker + backend |
| `searxng`       | `searxng/searxng:latest` | 8080 | Local web search proxy |
| `celery_worker` | Built from `Dockerfile.mineru` | — | MinerU + embedding + summarization |
| `api`           | Built from `Dockerfile` | 8000 | FastAPI backend |

## Project Directory Tree

```
backend/
├── docker-compose.yml
├── Dockerfile
├── Dockerfile.mineru
├── pyproject.toml
├── .env.example
├── .gitignore
├── docker/
│   ├── postgres/init/01-enable-pgvector.sql
│   └── searxng/settings.yml
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── deps.py
│   │   ├── errors.py
│   │   └── v1/
│   │       ├── router.py
│   │       └── endpoints/
│   │           ├── health.py
│   │           ├── documents.py
│   │           ├── chunks.py
│   │           ├── ask.py
│   │           └── search.py
│   ├── core/
│   │   ├── config.py
│   │   ├── lifecycle.py
│   │   ├── logging.py
│   │   ├── paths.py
│   │   └── celery_app.py
│   ├── database/
│   │   ├── schema.sql
│   │   ├── migrations.py
│   │   ├── connection.py
│   │   ├── pgvector.py
│   │   ├── transactions.py
│   │   └── repositories/
│   │       ├── documents.py
│   │       ├── chunks.py
│   │       ├── embeddings.py
│   │       ├── assets.py
│   │       ├── conversations.py
│   │       ├── figure_descriptions.py
│   │       └── section_summaries.py
│   ├── extraction/
│   │   ├── pipeline.py
│   │   ├── pipeline_sync.py
│   │   ├── mineru_client.py
│   │   ├── chunker.py
│   │   ├── normalizer.py
│   │   ├── assets.py
│   │   └── jobs.py
│   ├── embeddings/
│   │   ├── model.py
│   │   ├── service.py
│   │   └── service_sync.py
│   ├── chat/
│   │   ├── orchestrator.py
│   │   ├── router.py
│   │   ├── prompts.py
│   │   ├── local_context.py
│   │   ├── global_context.py
│   │   ├── overview_context.py
│   │   ├── external_context.py
│   │   ├── research_agent.py
│   │   ├── guardrail.py
│   │   └── citations.py
│   ├── llm/
│   │   ├── ollama_client.py
│   │   ├── vlm_client.py
│   │   ├── model_registry.py
│   │   └── multimodal.py
│   ├── search/
│   │   ├── searxng_client.py
│   │   └── ranking.py
│   ├── schemas/
│   │   ├── common.py
│   │   ├── documents.py
│   │   ├── chunks.py
│   │   ├── chat.py
│   │   └── search.py
│   ├── services/
│   │   ├── documents.py
│   │   ├── chunks.py
│   │   ├── ingestion.py
│   │   ├── retrieval.py
│   │   ├── reading_order.py
│   │   └── image_service.py
│   ├── summarization/
│   │   ├── section_summarizer_sync.py
│   │   └── figure_describer_sync.py
│   ├── workers/
│   │   ├── tasks.py
│   │   └── ingestion_worker.py
│   └── storage/
│       ├── documents/
│       ├── extracted/
│       ├── images/
│       ├── assets/
│       └── logs/
└── tests/
    ├── conftest.py
    ├── test_chunk_sequence.py
    ├── test_vector_retrieval.py
    ├── test_context_router.py
    ├── test_ingestion_pipeline.py
    └── test_subthread_conversations.py
```

## Core API Contracts

```
GET    /api/v1/health
POST   /api/v1/papers/upload
GET    /api/v1/papers
GET    /api/v1/papers/{paper_id}
GET    /api/v1/papers/{paper_id}/progress
GET    /api/v1/papers/{paper_id}/raw
DELETE /api/v1/papers/{paper_id}
POST   /api/v1/papers/{paper_id}/rechunk
POST   /api/v1/papers/{paper_id}/reextract
POST   /api/v1/papers/{paper_id}/regenerate-summaries
POST   /api/v1/papers/{paper_id}/reconstruct-reading-order
GET    /api/v1/papers/{paper_id}/chunks
GET    /api/v1/papers/{paper_id}/chunks/{sequence_order}
GET    /api/v1/papers/{paper_id}/figure-descriptions
POST   /api/v1/papers/{paper_id}/ask
GET    /api/v1/papers/{paper_id}/chat
GET    /api/v1/papers/{paper_id}/conversations
GET    /api/v1/search/vector
GET    /api/v1/search/web
```

## Critical Architectural Rules

1. `sequence_id` is the source of truth for physical document order.
2. pgvector similarity must never overwrite or redefine sequence order.
3. API routers remain thin.
4. MinerU extraction completes before embedding generation runs.
5. After embedding completes, section summarization + VLM figure descriptions run automatically.
6. `/ask` records the selected context, router reason, retrieved sources, and selected model.
7. Local-first behavior means the app works without cloud services.
8. SearXNG is the only external retrieval path.
9. Conversation compaction prevents context overflow for long chats.
10. Sub-threads isolate tangents from the main chat via `parent_turn_id`.