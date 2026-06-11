# Architecture

9XAIPal is a single-tenant, local-first app. Everything runs on one machine:
the frontend, the FastAPI backend, the extractor, the LLM, the embedding
model, and the database. The web search service (SearXNG) is the only
component that talks to the public internet, and only when the chat router
decides a query is about external information.

## Component map

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Frontend (Vite/React)                       │
│                                                                          │
│  LibraryView ──► ReadingView ──► ChatPane                                │
│       │             │ /chunks/{seq}    │ /ask                            │
│       │ /papers     ▼                  ▼                                 │
│       ▼          structural          assistant answer                    │
│   list/upload    one-at-a-time       grounded in chunks                  │
└────────────────────────┬─────────────────────────────────────────────────┘
                         │ HTTP (/api/v1/* and /static/*)
                         │ proxied by Vite to :8000
                         ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         FastAPI backend (port 8000)                      │
│                                                                          │
│  api/v1/endpoints   →   services    →   database/repositories            │
│      │                      │                    │                       │
│      │                      ▼                    ▼                       │
│      │             chat/orchestrator      Postgres + pgvector            │
│      │              ├ router (LOCAL/GLOBAL/OVERVIEW/EXTERNAL)            │
│      │              ├ local_context (chunk + image)                      │
│      │              ├ global_context (pgvector search)                   │
│      │              ├ overview_context (section_summaries)               │
│      │              ├ external_context (SearXNG)                         │
│      │              ├ research_agent (iterative loop)                    │
│      │              └ guardrail (IT-topic gate)                          │
│      │                                                                   │
│      ▼                                                                   │
│  workers/tasks.py → Celery → MinerU / embedding / summarization         │
└─────────┬─────────────────┬─────────────────────┬────────────────────────┘
          │                 │                     │
          ▼                 ▼                     ▼
   Ollama OR cloud API   MinerU CLI            SearXNG (local)
   (chat + vlm +        (PDF → md + imgs)     (web search proxy)
    embedding model;
    auto-detected by
    app/llm/resolver.py)
```

## Process model

The web API runs in `uvicorn app.main:app`.

Heavy work (PDF ingestion with MinerU, embedding generation, section
summarization, figure description, reading order reconstruction) is offloaded
to **Celery workers** (Redis broker). See:
- `app/workers/tasks.py`
- `docker-compose.yml` (celery_worker service)
- `app/core/celery_app.py`

This gives better isolation, crash recovery, and the ability to scale workers
independently. The old pure in-process `BackgroundTasks` + `asyncio.Queue`
design has been replaced by Celery.

## Layering and dependency direction

```
endpoints  →  services  →  repositories  →  SQL
       \         │
        \        └→ chat.orchestrator / extraction.pipeline (use-cases)
         \
          → schemas (pydantic, wire format only)
```

- **`endpoints/`** is thin: parse + validate input, call a service or
  use-case, shape the response.
- **`services/`** is the use-case layer. It owns transactions and
  cross-cutting workflows (`documents`, `ingestion`, `chunks`, `retrieval`).
- **`database/repositories/`** is pure SQL via `sqlalchemy.text`. Returns
  plain dicts. No business logic.
- **`schemas/`** are pydantic models that mirror what crosses the wire.
- **`chat/`** and **`extraction/`** are the two non-trivial pipelines and
  are documented in their own files.

## Why local-first

- **Privacy** — papers and chats never leave the machine.
- **Latency** — the LLM and vector search are colocated with the data.
- **Cost** — no per-token billing.

The price is that the app inherits the cold-start latency of the local LLM
(model load, GPU warmup) and the throughput limits of one machine.

## Failure model

| Failure                              | Behavior                                  |
| ------------------------------------ | ----------------------------------------- |
| Postgres unreachable                 | `/health` reports `database:"unavailable"`, requests 5xx |
| Ollama down                          | `LLM_PROVIDER=auto` falls back to the first cloud API key in `.env`; with no key, requests answer 503 `NO_LLM_CONFIGURED` with configure-me instructions |
| SearXNG down                         | EXTERNAL branch returns empty results → answer is ungrounded but not crashing |
| MinerU exits non-zero                | Pipeline catches `MinerUError`, marks job + doc `failed`, frontend polling exits |
| Worker crashes                       | Celery auto-restarts; task is retried |
| Backend restart mid-ingestion        | Pipeline run is lost; document is left mid-status. Manual rerun via re-extract endpoint. |