# 9XAIPal — Production Handover README

> **Audience.** Engineering / QA / production team that needs to bring this
> system up on a fresh machine, exercise every feature, and reason about every
> moving part without spelunking the codebase first. Nothing in this document
> is glossed; every dependency, env var, table, endpoint, code path, and known
> gap is named.

---

## Table of Contents

1.  [What 9XAIPal is](#1-what-9xaipal-is)
2.  [Repository layout (every directory)](#2-repository-layout-every-directory)
3.  [Runtime topology & ports](#3-runtime-topology--ports)
4.  [Prerequisites & external services](#4-prerequisites--external-services)
5.  [Environment variables (every key)](#5-environment-variables-every-key)
6.  [Bringing the stack up](#6-bringing-the-stack-up)
7.  [Verifying installation](#7-verifying-installation)
8.  [Backend application reference](#8-backend-application-reference)
9.  [Database schema (every table & column)](#9-database-schema-every-table--column)
10. [Storage layout (every directory on disk)](#10-storage-layout-every-directory-on-disk)
11. [HTTP API reference (every endpoint)](#11-http-api-reference-every-endpoint)
12. [Ingestion pipeline (step by step)](#12-ingestion-pipeline-step-by-step)
13. [Chat orchestration & /ask flow](#13-chat-orchestration--ask-flow)
14. [Domain guardrail & cross-field policy](#14-domain-guardrail--cross-field-policy)
15. [AI backend & models (auto-detection, chat, VLM, embedding)](#15-ai-backend--models-auto-detection-chat-vlm-embedding)
16. [Frontend application reference](#16-frontend-application-reference)
17. [Background workers (Celery)](#17-background-workers-celery)
18. [Logging & tracing](#18-logging--tracing)
19. [Failure modes & recovery](#19-failure-modes--recovery)
20. [Security notes](#20-security-notes)
21. [Production test plan (manual + automated)](#21-production-test-plan-manual--automated)
22. [Operations playbook](#22-operations-playbook)
23. [Known gaps & future work](#23-known-gaps--future-work)

---

## 1. What 9XAIPal is

9XAIPal is a **local-first, single-tenant research assistant for reading
scientific / technical PDFs**. The user drops a PDF; the system extracts it
with MinerU, splits it into *structural* chunks (headings, paragraphs, math,
tables, figures), embeds each chunk with a local embedding model into
PostgreSQL + pgvector, renders the paper one chunk at a time, and answers
questions about it with a locally-running Gemma 4 model. The only component
that ever reaches the public internet is a local SearXNG metasearch proxy
(invoked only when chat routing decides the question is about external
information).

The product has three "halves":

1.  **Library / upload** — drag a PDF, watch a live `extracting → chunking →
    embedding → complete` progress overlay, get a clickable card.
2.  **Reading view** — a granular "reveal-next-chunk" reader optimized for deep
    reading, with KaTeX math, MinerU figures, and a contextual right-pane chat.
3.  **Chat (`/ask`)** — a routed orchestrator that picks the best context
    source for each question (`LOCAL`, `GLOBAL`, `OVERVIEW`, `EXTERNAL`),
    optionally runs an iterative research loop, and returns a grounded answer
    with interactive citations.

---

## 2. Repository layout (every directory)

```
9XAIPal/
├── backend/                    # FastAPI app + Celery workers
│   ├── Dockerfile              # backend image (uvicorn or celery worker)
│   ├── Dockerfile.mineru       # worker image with MinerU models baked in
│   ├── docker-compose.yml      # postgres + redis + searxng + celery_worker + api
│   │                           #   + frontend-build (one-shot SPA) + autoheal watchdog
│   ├── start-lan-server.sh     # temporary LAN server (full stack on one port; see §6.6)
│   ├── docker/                 # service init configs
│   │   ├── postgres/init       # initdb scripts (extensions, roles)
│   │   └── searxng/settings.yml
│   ├── pyproject.toml          # project + dev deps
│   ├── requirements.txt        # mirror of runtime deps for Docker build
│   ├── uv.lock                 # uv-resolved lockfile
│   ├── .env                    # local secrets (gitignored) — must be created
│   ├── .example.env            # canonical template, safe to copy
│   ├── tests/                  # pytest suite (see §21)
│   ├── docs/                   # internal architecture / migrations notes
│   └── app/
│       ├── main.py             # FastAPI entrypoint + CORS + static mounts
│       ├── api/                # HTTP layer
│       │   ├── deps.py         # FastAPI dependencies (get_db, get_settings)
│       │   ├── errors.py       # DocumentNotFound, ChunkNotFound + handlers
│       │   └── v1/
│       │       ├── router.py   # combines endpoint groups under /api/v1
│       │       └── endpoints/
│       │           ├── health.py     # GET /health
│       │           ├── documents.py  # POST /papers/upload, GET/DELETE /papers/*
│       │           ├── chunks.py     # GET /papers/{id}/chunks[/seq]
│       │           ├── ask.py        # POST /papers/{id}/ask, GET /chat, /conversations
│       │           └── search.py     # GET /search/vector, /search/web
│       ├── chat/                       # /ask orchestration
│       │   ├── orchestrator.py         # handle_ask: route → retrieve → LLM → trace
│       │   ├── router.py               # LOCAL / GLOBAL / OVERVIEW / EXTERNAL classifier
│       │   ├── guardrail.py            # IT-topic gate
│       │   ├── local_context.py        # current chunk + neighbors + images
│       │   ├── global_context.py       # pgvector top-K + asset surfacing
│       │   ├── overview_context.py     # pre-computed section_summaries path
│       │   ├── external_context.py     # SearXNG + ranking
│       │   ├── research_agent.py       # iterative research loop (model-driven)
│       │   ├── prompts.py              # every system prompt + formatters
│       │   └── citations.py            # citation builders for chunks / web / overview
│       ├── core/
│       │   ├── config.py               # pydantic-settings (env-driven)
│       │   ├── lifecycle.py            # startup: dirs, DB check, migrations
│       │   ├── celery_app.py           # Celery wiring (broker / backend / serializer)
│       │   ├── logging.py              # get_logger() helper (uvicorn-friendly)
│       │   └── paths.py                # storage_root, images_dir(), assets_dir(), etc.
│       ├── database/
│       │   ├── schema.sql              # canonical schema (idempotent CREATE IF NOT EXISTS)
│       │   ├── migrations.py           # applies schema.sql at lifespan startup
│       │   ├── connection.py           # async engine + session factory; sync mirror for Celery
│       │   ├── pgvector.py             # vector-type registration glue
│       │   ├── transactions.py         # commit helpers
│       │   └── repositories/           # raw-SQL repos (return dicts)
│       │       ├── documents.py
│       │       ├── chunks.py
│       │       ├── embeddings.py       # pgvector search SQL
│       │       ├── assets.py           # chunk_assets reads
│       │       ├── conversations.py    # turns + traces + history
│       │       ├── figure_descriptions.py
│       │       └── section_summaries.py
│       ├── embeddings/                  # embedding services (Ollama or cloud via resolver; sync + async)
│       ├── extraction/
│       │   ├── mineru_client.py         # subprocess wrapper around `mineru` CLI
│       │   ├── chunker.py               # markdown → structural chunks
│       │   ├── normalizer.py            # math + unicode + footnote cleanup
│       │   ├── assets.py                # move_asset_to_storage (image dedup + rename)
│       │   ├── jobs.py                  # JobStatus enum
│       │   ├── pipeline.py              # async pipeline (legacy / fast path)
│       │   └── pipeline_sync.py         # sync pipeline used by Celery
│       ├── llm/
│       │   ├── resolver.py              # AI backend auto-detection: Ollama → cloud keys → error (see §15)
│       │   ├── client.py                # backend-agnostic chat/stream/sync entry points (role-based)
│       │   ├── ollama_client.py         # Ollama-specific transport (POST /api/chat + /api/tags)
│       │   └── multimodal.py            # build_multimodal_messages (text + base64 images)
│       ├── schemas/                     # pydantic v2 request/response shapes
│       │   ├── common.py                # HealthResponse, …
│       │   ├── documents.py             # DocumentResponse, DocumentListResponse, upload
│       │   ├── chunks.py                # ChunkResponse, ChunkListResponse
│       │   ├── chat.py                  # AskResponse, Citation
│       │   └── search.py                # SearchResponse
│       ├── search/                      # SearXNG client + result ranking
│       ├── services/                    # use-case layer (orchestrates repos + side effects)
│       │   ├── documents.py             # create/list/get/delete
│       │   ├── chunks.py                # paginated reads + shaping
│       │   ├── ingestion.py             # job lifecycle + status updates
│       │   ├── retrieval.py             # search_chunks (embed query + pgvector search)
│       │   ├── reading_order.py         # LLM-driven reading order reconstruction
│       │   └── image_service.py         # image fetch + thumbnail helpers
│       ├── summarization/
│       │   ├── section_summarizer_sync.py    # high-quality hierarchical summaries
│       │   └── figure_describer_sync.py      # VLM descriptions of figures/diagrams
│       ├── workers/
│       │   ├── tasks.py                  # @celery_app.task — process_ingestion, embed_document,
│       │   │                             #   generate_section_summaries, reconstruct_reading_order
│       │   └── ingestion_worker.py       # local in-process loop fallback
│       └── storage/                      # runtime data root (created at startup)
│           ├── documents/                # uploaded PDFs (named <uuid>.pdf)
│           ├── extracted/<doc_id>/...    # raw MinerU output (md + intermediate images)
│           ├── images/<doc_id>/...       # curated, served chunk images
│           ├── assets/<doc_id>.pdf       # PDF copies keyed by doc_id (for /raw + /static)
│           └── logs/                     # reserved
└── frontend/                            # Vite + React + Tailwind
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts                   # proxies /api + /static to :8000
    ├── tailwind.config.js, postcss.config.js
    ├── index.html
    └── src/
        ├── main.tsx                     # React 19 bootstrap
        ├── App.tsx                      # hash-routed state machine: library / processing / reading / pdf-viewer
        ├── api.ts                       # typed fetch client (every endpoint)
        ├── types.ts                     # Paper, ChunkData, Citation, ChatMessage, …
        ├── data.ts                      # static fallback library (offline mode)
        ├── index.css                    # design tokens (CSS variables)
        ├── components/                  # Icons, LogoMark
        └── views/
            ├── LibraryView.tsx          # grid/list + drag-drop upload + sort/search
            ├── ProcessingOverlay.tsx    # live extracting/chunking/embedding status
            ├── ReadingView.tsx          # granular chunk reveal + ChatPane
            ├── ChatPane.tsx             # turns + ReactMarkdown + citation chips
            ├── PdfViewer.tsx            # in-browser raw-PDF viewer (react-pdf)
            └── RawFilesPanel.tsx        # slide-over with all uploaded PDFs
```

---

## 3. Runtime topology & ports

```
            ┌────────────────────────────────┐
            │  Browser (http://localhost:5173)│
            └──────────────┬─────────────────┘
                           │ /api, /static  (Vite proxy)
                           ▼
            ┌────────────────────────────────┐
            │  FastAPI (:8000)               │
            │   - /api/v1/*                  │
            │   - /static/{images,extracted, │
            │             assets}            │
            └───┬───────────────┬────────────┘
                │ Postgres     │ Celery .delay()
                ▼              ▼
   ┌──────────────────┐  ┌──────────────────┐
   │ pgvector (5432)  │  │ Redis (6379)     │
   └──────────────────┘  └────────┬─────────┘
                                  ▼
                       ┌────────────────────────┐
                       │ Celery worker          │
                       │  - process_ingestion   │
                       │  - embed_document      │
                       │  - generate_section_…  │
                       │  - reconstruct_reading_│
                       └──┬───────────┬─────────┘
                          │           │
                          ▼           ▼
                  ┌──────────────┐  ┌─────────────────────────┐
                  │ MinerU CLI   │  │ Ollama (:11434)         │
                  │ (subprocess) │  │  - chat/VLM model       │
                  └──────────────┘  │  - embedding model      │
                                    │ OR cloud API fallback   │
                                    │ (OpenAI/Anthropic/      │
                                    │  Gemini/xAI/DeepSeek)   │
                                    └─────────────────────────┘

                  ┌──────────────────────┐
                  │ SearXNG (:8080)      │  (only when route=EXTERNAL or
                  └──────────────────────┘   user explicitly invokes another field)
```

| Service        | Default URL                | Required | Notes                                                         |
| -------------- | -------------------------- | -------- | ------------------------------------------------------------- |
| FastAPI        | `http://localhost:8000`    | Yes      | `uvicorn app.main:app`                                        |
| Vite dev       | `http://localhost:5173`    | Yes (dev)| Proxies `/api` and `/static` to `:8000`                       |
| PostgreSQL     | `localhost:5432`           | Yes      | `pgvector/pgvector:pg16` image; `vector` + `uuid-ossp`        |
| Redis          | `localhost:6379`           | Yes      | Celery broker + result backend                                |
| Ollama         | `http://localhost:11434`   | Optional*| Hosts chat/VLM + embedding models                             |
| MinerU CLI     | binary on `$PATH` (`mineru`)| Yes     | Optional PyMuPDF fallback if `ALLOW_PYMUPDF_FALLBACK=true`     |
| SearXNG        | `http://localhost:8080`    | Optional | Only invoked for EXTERNAL / cross-field / empty-paper contexts |

\* One AI backend is required: either Ollama **or** a cloud API key — see §15
for the `LLM_PROVIDER=auto` detection chain.

---

## 4. Prerequisites & external services

### Host-level

| Tool        | Version | Why                                          |
| ----------- | ------- | -------------------------------------------- |
| Python      | 3.11+   | Backend                                      |
| Node.js     | 18+     | Frontend (Vite 6, React 19)                  |
| Docker + Compose | latest | Postgres / Redis / SearXNG / Celery worker |
| Ollama      | latest  | Local LLM + embedding host (optional with a cloud API key) |
| MinerU      | 3.2+    | `mineru` CLI; `magic-pdf` 0.x is **not** supported |
| Postgres    | 15+ (16 in compose) | with `pgvector` and `uuid-ossp`     |

### Ollama models (one-time pull — skip if using a cloud API key)

```bash
ollama pull gemma4:31b-cloud  # or your chosen chat/VLM model (CHAT_MODEL)
ollama pull qwen3-embedding   # embedding model (EMBEDDING_MODEL)
```

### MinerU

Install per upstream docs (CUDA recommended). First run downloads
~5 GB of weights from Hugging Face; `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN`
must be set in the env (see §5).

---

## 5. Environment variables (every key)

Loaded by `app/core/config.py` (pydantic-settings). Source is `backend/.env`
(falls back to env). `extra="ignore"` so unknown keys won't break startup.

| Key                        | Default                              | Required | Purpose                                                                 |
| -------------------------- | ------------------------------------ | -------- | ----------------------------------------------------------------------- |
| `APP_ENV`                  | `local`                              | No       | Free-form environment tag (logging only).                                |
| `DEBUG`                    | `false`                              | No       | When `true` enables verbose logging from `app.core.logging`.             |
| `POSTGRES_HOST`            | `localhost`                          | Yes      |                                                                         |
| `POSTGRES_PORT`            | `5432`                               | Yes      |                                                                         |
| `POSTGRES_DB`              | `9xaipal`                            | Yes      |                                                                         |
| `POSTGRES_USER`            | `9xaipal`                            | Yes      |                                                                         |
| `POSTGRES_PASSWORD`        | `9xaipal_dev_password`               | Yes      | **Rotate for production.**                                              |
| `STORAGE_ROOT`             | `app/storage`                        | No       | Root for all on-disk artifacts. Relative paths resolve from CWD.        |
| `MINERU_BINARY`            | `mineru`                             | No       | Absolute path to the `mineru` CLI if not on `$PATH`.                    |
| `MINERU_LANG`              | `en`                                 | No       | OCR language hint.                                                      |
| `ALLOW_PYMUPDF_FALLBACK`   | `false`                              | No       | When `true`, falls back to text-only PyMuPDF if MinerU missing.         |
| `OLLAMA_BASE_URL`          | `http://localhost:11434`             | Yes      | In Docker compose worker: `http://host.docker.internal:11434`           |
| `CHAT_MODEL`               | `gemma4:26b`                         | Yes      | Chat/answer model **for Ollama** (also used by `custom`). Cloud providers use their own `*_CHAT_MODEL`. |
| `VLM_MODEL`                | (empty → reuses `CHAT_MODEL`)        | No       | Vision model for the figure describer / image questions (Ollama).       |
| `EMBED_MAX_CHARS`          | `3000`                               | No       | Char cap per chunk sent to the embedder (raise for cloud embedders).    |
| `EMBEDDING_MODEL`          | `qwen3-embedding`                    | Yes      | Embedding model **for Ollama** (also used by `custom`). Any dimension: outputs are truncated+renormalized (MRL) or zero-padded to `VECTOR_DIMENSION`. |
| `LOCAL_CONTEXT_WINDOW`     | `3`                                  | No       | LOCAL: ± chunks around current.                                         |
| `VECTOR_DIMENSION`         | `1024`                               | Yes      | Stored embedding size. Keep ≤ 2000 so the pgvector HNSW index applies. Changing it auto-migrates + re-embeds on next start. |
| `LLM_PROVIDER`             | `auto`                               | No       | `auto` = Ollama if reachable, else first cloud key found (openai → anthropic → gemini → xai → deepseek), else a clear configure-me error. Pin with `ollama` \| `openai` \| `anthropic` \| `gemini` \| `xai` \| `deepseek` \| `custom`. |
| `OPENAI_API_KEY` … `DEEPSEEK_API_KEY` | (none)                    | No       | Per-provider keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`. In `auto` mode the first one set (in that order) is used when Ollama is off. |
| `OPENAI_CHAT_MODEL` …      | `gpt-4o`, `claude-sonnet-4-6`, `gemini-2.5-flash`, `grok-4`, `deepseek-chat` | No | Chat model per cloud provider. DeepSeek has no vision → figure images can't be described. |
| `LLM_API_KEY`              | (none)                               | No       | Generic key for a pinned `LLM_PROVIDER` (per-provider keys also work).  |
| `LLM_BASE_URL`             | (provider default)                   | `custom` only | Any OpenAI-compatible endpoint (OpenRouter, vLLM, …) or URL override. |
| `EMBEDDING_PROVIDER`       | `auto`                               | No       | `auto` = Ollama if reachable, else OpenAI, else Gemini (the only clouds with embedding APIs). Pin with `ollama` \| `openai` \| `gemini` \| `custom` — pinning triggers an automatic re-embed when the stored model differs. |
| `OPENAI_EMBEDDING_MODEL`   | `text-embedding-3-small`             | No       | Embedding model when OpenAI is active. `GEMINI_EMBEDDING_MODEL` defaults to `gemini-embedding-001`. |
| `EMBEDDING_API_KEY`        | (falls back to per-provider / `LLM_API_KEY`) | No | Override only.                                                          |
| `EMBEDDING_BASE_URL`       | (falls back / provider default)      | No       | Override only.                                                          |
| `SEARXNG_URL`              | `http://localhost:8080`              | No       | Disable EXTERNAL path by pointing at an unreachable URL.                |
| `MAX_UPLOAD_SIZE_MB`       | `100`                                | No       | Hard cap on POST `/papers/upload` body size.                            |
| `REDIS_URL`                | `redis://localhost:6379/0`           | Yes      | Celery broker + result backend.                                         |
| `CELERY_BROKER_URL`        | (falls back to `REDIS_URL`)          | No       | Override only.                                                          |
| `CELERY_RESULT_BACKEND`    | (falls back to `REDIS_URL`)          | No       | Override only.                                                          |
| `OLLAMA_FLASH_ATTENTION`   | `0`                                  | Yes (Ollama host) | Required for quantized gemma4 to be stable. Set on the Ollama process. |
| `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` | (none)                    | Yes (first MinerU run) | Used by MinerU to download weights. **Rotate before prod.**     |

> ⚠️ The shipped `backend/.env` contains a development HF token. **It must be
> rotated at https://huggingface.co/settings/tokens before production
> deployment** and never committed to a public repo.

### Using a cloud LLM instead of Ollama (GPT / Claude / Gemini / Grok / DeepSeek)

All five providers are reached through the OpenAI chat-completions protocol
(`app/llm/client.py`); which backend answers is decided by `app/llm/resolver.py`.
With the default `LLM_PROVIDER=auto` you don't switch anything manually:

1. **Ollama running?** → it is used, with `CHAT_MODEL` / `VLM_MODEL` /
   `EMBEDDING_MODEL` from `.env`.
2. **Ollama off?** → the first cloud API key found is used (openai →
   anthropic → gemini → xai → deepseek), with that provider's own
   `*_CHAT_MODEL` (so your Ollama tags are never sent to a cloud API).
3. **Neither?** → requests fail with explicit instructions to add an API key
   or an Ollama connection (also logged at startup).

So going cloud is just:

```env
# Paste whichever key you end up buying — nothing else to change:
ANTHROPIC_API_KEY=sk-ant-...
# (optional) override the default model for that provider:
# ANTHROPIC_CHAT_MODEL=claude-sonnet-4-6
```

then stop Ollama (or pin `LLM_PROVIDER=anthropic` to force cloud even while
Ollama runs). Embeddings follow the same chain but only OpenAI and Gemini
offer embedding APIs; when you move permanently, pin `EMBEDDING_PROVIDER=openai`
(or `gemini`) — on the next start stale vectors are wiped and the library
re-embeds automatically with the new model (summaries and figure descriptions
are cached and don't re-run). In `auto` mode a model mismatch only logs a loud
warning, so a temporarily-down Ollama can't wipe your embeddings.

Default cloud models: `gpt-4o` (openai), `claude-sonnet-4-6` (anthropic),
`gemini-2.5-flash` (gemini), `grok-4` (xai), `deepseek-chat` (deepseek — no
vision, so figure images can't be described). `LLM_PROVIDER=custom` +
`LLM_BASE_URL` works with any OpenAI-compatible server and uses `CHAT_MODEL`.
Streaming (`POST /papers/{id}/ask/stream`, SSE) works on all providers
including Ollama.

---

## 6. Bringing the stack up

### 6.1 Cold-start checklist

1. Install Postgres + create role/DB matching the env, or run the bundled
   compose service.
2. Install Redis (`brew install redis` / `apt install redis-server`) or use
   the compose service.
3. Install Ollama; pull your `CHAT_MODEL` and `EMBEDDING_MODEL` (e.g.
   `gemma4:31b-cloud` + `qwen3-embedding`). **Or skip Ollama entirely** and
   paste one cloud API key in `.env` — see §15: with `LLM_PROVIDER=auto` the
   backend picks Ollama when reachable, else the first cloud key it finds.
4. Install MinerU; verify `mineru --help` works on the shell that the Celery
   worker will inherit. (The Docker worker bakes MinerU + models into
   `Dockerfile.mineru`, so nothing to install when running via compose.)
5. Install SearXNG (compose handles this).
6. Copy `backend/.example.env` → `backend/.env` and edit secrets.

### 6.2 Backend (host)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Postgres reachable (host = $POSTGRES_HOST) + DB created.
# Schema is applied idempotently at startup by core/lifecycle.py.

# Start the API
uvicorn app.main:app --reload --port 8000

# Start the Celery worker (separate shell, same venv)
celery -A app.core.celery_app worker --loglevel=info
```

### 6.3 Ancillary services via Docker (recommended)

```bash
cd backend
docker compose up -d postgres redis searxng
# (the celery_worker service is also defined; you can run it in compose too)
docker compose up -d celery_worker

# OR: the full server in containers (UI + API on :8000, see DEPLOYMENT.md)
docker compose --profile server up -d --build
```

`docker-compose.yml` mounts `./app/storage` so on-disk artifacts persist
across container restarts and are shared with the host-side FastAPI process.

**Auto-recovery (two layers).** Every long-running service (`postgres`,
`redis`, `searxng`, `celery_worker`, `api`) has `restart: unless-stopped`, so
a container that **crashes or exits** (e.g. the worker OOM-killed by a huge
book, exit 137) is restarted by Docker automatically — uploads queued in
Redis survive and resume. Separately, the `autoheal` watchdog service
(`willfarrell/autoheal`) restarts any container labeled `autoheal=true`
(`api`, `postgres`, `redis`) whose **healthcheck turns unhealthy** — that
covers the failure mode `restart:` can't see: a container that is still
running but hung/not responding. Autoheal needs the Docker socket mounted
(`/var/run/docker.sock`), which is why it can issue restarts. Data volumes
are never touched by either mechanism.

### 6.4 Frontend

```bash
cd frontend
npm install
npm run dev          # Vite at :5173 (proxies /api and /static to :8000)
# production build:
npm run build        # outputs dist/
npm run preview      # serves dist/ for smoke testing
```

### 6.5 Lifespan startup order (`app/core/lifecycle.py`)

On `uvicorn` start the lifespan:

1. Configures logging (`core/logging.py`).
2. Warns if Postgres still uses the default development password.
3. Ensures every storage subdir exists (`ensure_storage_dirs()` →
   `documents/`, `extracted/`, `images/`, `assets/`, `logs/`).
4. Mounts the built React SPA at `/` when `SERVE_FRONTEND=true` and a dist
   exists (single-port server mode).
5. Calls `verify_connection()` against Postgres.
6. Runs `apply_migrations()` (executes `database/schema.sql`).
7. **Reports the AI backend** (`_report_ai_backend()`): resolves the active
   LLM via `llm/resolver.py` and logs which one auto-detection picked. If
   nothing is usable it logs the exact "put your API key or your Ollama
   connection" instructions — never fatal; stored papers still serve.
8. Syncs the pgvector column to `VECTOR_DIMENSION` and ensures the HNSW
   index. If the dimension changed, all embeddings are wiped and re-queued.
9. If the dimension did NOT change, runs `_check_embedding_model_switch()`:
   compares stored `embedding_model` values against the active embedding
   target. A mismatch with a **pinned** `EMBEDDING_PROVIDER` wipes stale
   vectors and re-embeds the library automatically (summaries / figure
   descriptions are prompt-hash cached and don't re-run); in `auto` mode it
   only warns, so a temporarily-down Ollama can never trigger a destructive
   re-embed.
10. (Workers are started by Celery, not by lifespan, in the production path.)

Shutdown disposes the async engine.

### 6.6 Temporary LAN server (`start-lan-server.sh`)

Run `backend/start-lan-server.sh` to turn this machine into a temporary
server that any device on the **same local network** (same Wi-Fi/Ethernet)
can reach. The script:

- Brings up the full Docker stack with the `server` profile (API + UI on one
  port, Postgres, Redis, SearXNG, MinerU-enabled Celery worker) and builds
  the React SPA inside a container (no Node.js needed on the host).
- Writes a **throwaway compose override** (mktemp) instead of editing the
  tracked compose file: it removes the upload cap (`MAX_UPLOAD_SIZE_MB`,
  default 100 GB ≈ unlimited), raises the MinerU wall-clock timeout to 4 h
  for huge books, and sets the OCR language (`MINERU_LANG`, default `en`).
  Everything else (images, restart policies, autoheal, provider env) comes
  from `docker-compose.yml` itself, so the script always inherits the latest
  stack definition.
- Clears any previous instance first (fixed container names would otherwise
  collide), waits for the SPA build and the `/api/v1/health` check, then
  prints the exact LAN URL (e.g. `http://192.168.1.42:8000`).
- Streams api + worker logs until you stop it. On Ctrl+C, kill, or closing
  the terminal it tears the whole stack down (`docker compose down`,
  **without** `-v` — data volumes and uploaded papers persist).

```bash
cd backend
./start-lan-server.sh
# optional knobs:
API_PORT=9000 MAX_UPLOAD_MB=2048 MINERU_TIMEOUT_SEC=7200 ./start-lan-server.sh
```

While the server is running, the auto-recovery described in §6.3 is active
(crashed containers restart; hung-unhealthy ones are restarted by autoheal).
Stopping the script is a deliberate stop — nothing restarts after that.

> **Network scope:** this only works on the same local network — the script
> detects your private LAN IP (e.g. `192.168.x.x`), which is not reachable
> from the internet. Exposing the app publicly needs port forwarding, a
> public IP, or a tunnel (e.g. ngrok) — plus real authentication (see §20).

---

## 7. Verifying installation

```bash
# 1. Health
curl -s http://localhost:8000/api/v1/health | jq
# expected: {"status":"ok","database":"ok","ollama":"ok","searxng":"ok"}

# 2. Upload a PDF
curl -s -F "file=@/path/to/paper.pdf" http://localhost:8000/api/v1/papers/upload | jq
# expected: 201 {"id":"<uuid>","status":"processing", ...}

# 3. Poll progress until status=complete
curl -s http://localhost:8000/api/v1/papers/<uuid>/progress | jq

# 4. Read the first structural chunk
curl -s http://localhost:8000/api/v1/papers/<uuid>/chunks/1 | jq

# 5. Ask a question
curl -s -X POST http://localhost:8000/api/v1/papers/<uuid>/ask \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the main contribution of this paper?"}' | jq
```

In the browser, open `http://localhost:5173`, drag a PDF onto the library
dropzone, wait for `extracting → chunking → embedding → complete`, then click
the card and ask questions in the right pane.

---

## 8. Backend application reference

### 8.1 Entrypoint (`app/main.py`)

- Constructs the FastAPI app with `lifespan=lifespan`.
- Adds CORS for `localhost:5173`, `localhost:3000`, `127.0.0.1:5173` (methods
  + headers wide-open).
- Mounts the v1 router under `/api/v1`.
- Registers `DocumentNotFound` and `ChunkNotFound` exception handlers.
- Static mounts (all `check_dir=False` so they survive a missing dir at boot):
  - `/static/images   → images_dir()`
  - `/static/extracted → extracted_dir()`
  - `/static/assets    → assets_dir()`
  - `/static/images/research → research_images_dir()` (research-agent images)

### 8.2 Dependency injection (`app/api/deps.py`)

- `get_db()` — yields an `AsyncSession` from the global async session factory.
- `get_settings()` — returns the singleton `Settings` instance.

### 8.3 Settings (`app/core/config.py`)

`Settings(BaseSettings)` with the keys documented in §5. Exposes derived
`database_url` (asyncpg) and `database_url_sync` (psycopg2) properties used by
the async API and Celery workers respectively.

### 8.4 Paths (`app/core/paths.py`)

All disk paths derive from `settings.storage_root`:

| Function              | Default path                        |
| --------------------- | ----------------------------------- |
| `documents_dir()`     | `<root>/documents`                  |
| `extracted_dir()`     | `<root>/extracted`                  |
| `images_dir()`        | `<root>/images`                     |
| `assets_dir()`        | `<root>/assets`                     |
| `research_images_dir()`| `<root>/images/research`           |
| `logs_dir()`          | `<root>/logs`                       |

`ensure_storage_dirs()` `mkdir(parents=True, exist_ok=True)`s every one of
these on startup and is called defensively before each upload.

### 8.5 Logging

`app.core.logging.get_logger(name)` returns a `logging.Logger` configured for
uvicorn. Every `/ask` step prints structured `ASK[stepN]` log lines used in
production debugging. Celery tasks log under `[celery]` prefixes.

---

## 9. Database schema (every table & column)

Canonical: `backend/app/database/schema.sql`. Applied at startup via
`apply_migrations()` (idempotent `CREATE … IF NOT EXISTS`).

Required extensions:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

### 9.1 `documents`

| Column                      | Type            | Notes                                                  |
| --------------------------- | --------------- | ------------------------------------------------------ |
| `id`                        | `UUID PK`       | `DEFAULT uuid_generate_v4()`.                          |
| `filename`                  | `TEXT`          | Opaque storage filename (`<uuid>.pdf`).                |
| `original_filename`         | `TEXT`          | User-provided filename (used by `/raw`).               |
| `file_size_bytes`           | `BIGINT`        |                                                        |
| `page_count`                | `INTEGER`       | Set by `pypdf` at end of pipeline.                     |
| `status`                    | `TEXT`          | `queued / complete / failed`.                          |
| `error_message`             | `TEXT`          | Last failure message.                                  |
| `created_at`                | `TIMESTAMPTZ`   |                                                        |
| `updated_at`                | `TIMESTAMPTZ`   | Bumped by `update_document_status`.                    |
| `reading_order`             | `JSONB`         | LLM-corrected sequence of chunk sequence_ids (two-col papers). |
| `reading_order_model`       | `TEXT`          |                                                        |
| `reading_order_updated_at`  | `TIMESTAMPTZ`   |                                                        |
| `extractor`                 | `TEXT`          | `mineru` or `pymupdf_fallback`. Surfaced in UI badge. |

### 9.2 `chunks`

| Column                | Type          | Notes                                              |
| --------------------- | ------------- | -------------------------------------------------- |
| `id`                  | `UUID PK`     |                                                    |
| `document_id`         | `UUID`        | FK → `documents.id` `ON DELETE CASCADE`.           |
| `sequence_id`         | `INTEGER`     | 1-based reading order.                             |
| `parent_sequence_id`  | `INTEGER`     | Reserved.                                          |
| `chunk_type`          | `TEXT`        | `text / heading / math / table / figure / footnote`.|
| `heading_path`        | `TEXT[]`      | Breadcrumb of H1..H6 titles.                       |
| `markdown`            | `TEXT`        | Normalized markdown body.                          |
| `plain_text`          | `TEXT`        | What we embed.                                     |
| `page_start`/`page_end`| `INTEGER`    | Currently nullable.                                |
| `bbox_json`           | `JSONB`       | Reserved for bounding boxes.                       |
| `token_count`         | `INTEGER`     | `≈ len(plain_text) / 4`.                           |
| `table_json`          | `JSONB`       | Populated for `chunk_type='table'`.                |
| `created_at`          | `TIMESTAMPTZ` |                                                    |

`UNIQUE(document_id, sequence_id)`. Index `idx_chunks_document_sequence`.

### 9.3 `chunk_embeddings`

| Column           | Type         | Notes                                   |
| ---------------- | ------------ | --------------------------------------- |
| `chunk_id`       | `UUID PK`    | FK → `chunks.id` cascade.               |
| `embedding`      | `vector(VECTOR_DIMENSION)` (default 1024) | Cosine search via `ORDER BY <=>`. |
| `embedding_model`| `TEXT`       |                                         |
| `created_at`     | `TIMESTAMPTZ`|                                         |

### 9.4 `chunk_assets`

| Column       | Type          | Notes                                                |
| ------------ | ------------- | ---------------------------------------------------- |
| `id`         | `UUID PK`     |                                                      |
| `chunk_id`   | `UUID`        | FK → `chunks.id` cascade.                            |
| `asset_type` | `TEXT`        | `image`, …                                           |
| `file_path`  | `TEXT`        | **Relative** to `images_dir()` — served at `/static/images/<file_path>`. |
| `mime_type`  | `TEXT`        |                                                      |
| `width`/`height`| `INTEGER`  | Reserved.                                            |
| `caption`    | `TEXT`        | Reserved.                                            |
| `created_at` | `TIMESTAMPTZ` |                                                      |

Index `idx_chunk_assets_chunk_id`.

### 9.5 `conversation_turns`

| Column            | Type         | Notes                                           |
| ----------------- | ------------ | ----------------------------------------------- |
| `id`              | `UUID PK`    |                                                 |
| `conversation_id` | `UUID`       | Groups turns into a thread (frontend-managed).  |
| `document_id`     | `UUID`       | FK → `documents.id` **`ON DELETE SET NULL`**.   |
| `role`            | `TEXT`       | `user / assistant / compaction`.                |
| `content`         | `TEXT`       |                                                 |
| `context_type`    | `TEXT`       | `LOCAL / GLOBAL / OVERVIEW / EXTERNAL / OUT_OF_SCOPE / COMPACTION`. |
| `router_reason`   | `TEXT`       |                                                 |
| `model`           | `TEXT`       |                                                 |
| `citations`       | `JSONB`      | Serialized list of `Citation` dicts.            |
| `created_at`     | `TIMESTAMPTZ` |                                                 |

Index `idx_conversation_turns_conversation(conversation_id, created_at)`.

### 9.6 `ask_traces`

| Column                  | Type          | Notes                                   |
| ----------------------- | ------------- | --------------------------------------- |
| `id`                    | `UUID PK`     |                                         |
| `conversation_turn_id`  | `UUID`        | FK → `conversation_turns.id` cascade.   |
| `context_type`          | `TEXT`        |                                         |
| `router_reason`         | `TEXT`        |                                         |
| `retrieved_chunk_ids`   | `UUID[]`      | Currently null — reserved.              |
| `model`                 | `TEXT`        |                                         |
| `prompt_tokens`         | `INTEGER`     | From Ollama.                            |
| `completion_tokens`     | `INTEGER`     |                                         |
| `latency_ms`            | `INTEGER`     | Wall-clock inside `handle_ask`.         |
| `created_at`            | `TIMESTAMPTZ` |                                         |

### 9.7 `ingestion_jobs`

| Column          | Type          | Notes                                                          |
| --------------- | ------------- | -------------------------------------------------------------- |
| `id`            | `UUID PK`     |                                                                |
| `document_id`   | `UUID`        | FK → `documents.id` cascade.                                   |
| `status`        | `TEXT`        | `queued / extracting / chunking / embedding / summarizing / complete / failed`. |
| `error_message` | `TEXT`        |                                                                |
| `started_at`    | `TIMESTAMPTZ` | Set on first non-queued transition.                            |
| `completed_at`  | `TIMESTAMPTZ` | Set on `complete` or `failed`.                                 |
| `created_at`    | `TIMESTAMPTZ` |                                                                |

Index `idx_ingestion_jobs_status`.

### 9.8 `section_summaries`

Pre-computed hierarchical overviews used by the `OVERVIEW` chat route.

| Column           | Type          | Notes                                                              |
| ---------------- | ------------- | ------------------------------------------------------------------ |
| `id`             | `UUID PK`     |                                                                    |
| `document_id`    | `UUID`        | FK → `documents.id` cascade.                                       |
| `section_id`     | `TEXT`        | Stable within-doc id (e.g. `h1-03-introduction`).                  |
| `level`          | `INTEGER`     | `0` = whole paper, `1` = H1, `2` = H2.                             |
| `heading_path`   | `TEXT[]`      | Heading breadcrumb at the time of summarization.                   |
| `sequence_start`/`sequence_end` | `INTEGER` | Inclusive source sequence range.                          |
| `summary_markdown` / `summary_plain` | `TEXT` |                                                            |
| `source_chunk_ids` | `UUID[]`    | Exact chunk IDs fed to the LLM. Drives citations.                  |
| `model`          | `TEXT`        |                                                                    |
| `prompt_hash`    | `TEXT`        | Hash of prompt template + version → drives invalidation.           |
| `created_at`     | `TIMESTAMPTZ` |                                                                    |

`UNIQUE(document_id, section_id, model)`. Indexes by `(document_id, level, sequence_start)` and `(document_id, created_at DESC)`.

### 9.9 `figure_descriptions`

VLM-generated technical descriptions of figures / diagrams / architectures.

| Column                       | Type          | Notes                                          |
| ---------------------------- | ------------- | ---------------------------------------------- |
| `id`                         | `UUID PK`     |                                                |
| `document_id`                | `UUID`        | FK → `documents.id` cascade.                   |
| `chunk_id`                   | `UUID`        | FK → `chunks.id` cascade.                      |
| `image_path`                 | `TEXT`        | Relative path under `images/`.                 |
| `description_markdown` / `description_plain` | `TEXT` |                                       |
| `source_sequence_start`/`source_sequence_end` | `INTEGER` |                                     |
| `referenced_by_chunk_ids`    | `UUID[]`      | Text chunks that mention this figure.          |
| `model`                      | `TEXT`        | e.g. `gemma4:26b`.                             |
| `prompt_hash`                | `TEXT`        |                                                |
| `created_at`                 | `TIMESTAMPTZ` |                                                |

`UNIQUE(chunk_id, model)`. Indexed by `(document_id, created_at DESC)` and `(chunk_id)`.

### 9.10 ERD summary

```
documents (1) ─< chunks ─< chunk_embeddings (1:1)
              \         \─< chunk_assets (N)
              \         \─< figure_descriptions (N)
              \
              \─< section_summaries (N)
              \─< ingestion_jobs (N)

conversation_turns ─< ask_traces (1:1 per assistant turn)
documents (1) ─< conversation_turns (SET NULL on delete)
```

All FKs cascade except `conversation_turns.document_id` which uses `SET NULL`
so chat history survives paper deletion.

---

## 10. Storage layout (every directory on disk)

Rooted at `STORAGE_ROOT` (default `backend/app/storage`).

```
<storage_root>/
├── documents/<storage_uuid>.pdf       # original upload, used by MinerU
├── extracted/<doc_id>/                # raw MinerU output (md + intermediates)
├── images/<doc_id>/<asset_uuid>.<ext> # served at /static/images/<doc_id>/<asset_uuid>.<ext>
├── images/research/<conv_id>/...      # research-agent-saved images
├── assets/<doc_id>.pdf                # PDF copy keyed by doc_id (predictable URL)
└── logs/                              # reserved
```

Sizing per paper (typical research PDF):

- `documents/<uuid>.pdf`: 5–30 MB.
- `assets/<doc_id>.pdf`: same bytes as above (duplicate copy by design).
- `extracted/<doc_id>/`: 2–5 MB.
- `images/<doc_id>/`: 50–500 KB per figure.

URL mapping:

| URL                                       | Disk path                                        |
| ----------------------------------------- | ------------------------------------------------ |
| `/static/images/<doc_id>/<asset>.png`     | `<root>/images/<doc_id>/<asset>.png`             |
| `/static/extracted/<doc_id>/...`          | `<root>/extracted/<doc_id>/...`                  |
| `/static/assets/<doc_id>.pdf`             | `<root>/assets/<doc_id>.pdf`                     |
| `/static/images/research/<conv_id>/<f>`   | `<root>/images/research/<conv_id>/<f>`           |
| `/api/v1/papers/<doc_id>/raw`             | `assets/<doc_id>.pdf` → fallback `documents/<filename>` |

`DELETE /papers/{id}` cleans up:

1. DB cascade across `chunks`, `chunk_embeddings`, `chunk_assets`,
   `figure_descriptions`, `section_summaries`, `ingestion_jobs`.
2. `documents/<filename>` (best effort).
3. `assets/<doc_id>.pdf` (best effort).
4. `extracted/<doc_id>/` (best effort, `shutil.rmtree`).
5. `images/<doc_id>/` (best effort, `shutil.rmtree`).

`conversation_turns` are kept with `document_id` nullified.

---

## 11. HTTP API reference (every endpoint)

All endpoints live under **`/api/v1`** (`app/api/v1/router.py`). Static
mounts are not under that prefix.

### 11.1 `GET /health`

Source: `endpoints/health.py`.

```json
{
  "status":   "ok" | "degraded",
  "database": "ok" | "unavailable",
  "ollama":   "ok" | "unavailable",
  "searxng":  "ok" | "unavailable"
}
```

`status="degraded"` whenever `database != "ok"`.

### 11.2 `POST /papers/upload`

Multipart upload (`file=<binary>`). Source: `endpoints/documents.py`.

- `201 Created` → `{id, filename, status:"processing", message}`.
- `413` if body > `MAX_UPLOAD_SIZE_MB`.
- `500` with traceback in `detail` on backend failure (intentional, helps
  diagnose Redis/Celery dispatch issues).

Side effects:

1. Writes `documents/<storage_uuid>.pdf`.
2. Inserts `documents` row (`status='queued'`).
3. Writes `assets/<doc_id>.pdf` (predictable copy).
4. Inserts `ingestion_jobs` row (`status='queued'`).
5. Dispatches `process_ingestion.delay(doc_id, job_id, filename)` to Celery.
   If Celery dispatch fails, the document is marked `failed` with a
   descriptive message ("Start Redis (e.g. via docker compose…)") so the
   frontend surfaces actionable text.

### 11.3 `GET /papers?limit=50&offset=0`

Returns `{documents: DocumentResponse[], total: int}`. `DocumentResponse`
shape matches §9.1 + `extractor`.

### 11.4 `GET /papers/{paper_id}`

Single `DocumentResponse` or `404`.

### 11.5 `GET /papers/{paper_id}/progress`

```json
{
  "paper_id":      "<uuid>",
  "status":        "queued|complete|failed",
  "job_status":    "queued|extracting|chunking|embedding|summarizing|complete|failed",
  "page_count":    <int|null>,
  "error_message": <string|null>,
  "extractor":     "mineru|pymupdf_fallback|null"
}
```

Polled by the frontend every 1 s during processing.

### 11.6 `GET /papers/{paper_id}/raw`

Streams the original PDF. `Content-Disposition: attachment; filename=<original>`.
Falls back from `assets/<id>.pdf` → `documents/<filename>`.

### 11.7 `DELETE /papers/{paper_id}` → `204 No Content`

Deletes DB row (cascade) and best-effort disk cleanup (see §10).

### 11.8 `POST /papers/{paper_id}/rechunk` → `200`

Re-runs the chunker on the cached `extracted/<id>/...` output without
re-running MinerU. Wipes chunks/embeddings/assets, re-inserts. Re-queues
embedding. `409` if no cached extraction.

### 11.9 `POST /papers/{paper_id}/reextract` → `202`

Wipes cached extraction + DB chunk-side artifacts and re-runs the full
pipeline (MinerU + chunker). Returns `{paper_id, status:"reextract_queued", job_id, message}`.

### 11.10 `POST /papers/{paper_id}/regenerate-summaries` → `202`

Dispatches `generate_section_summaries`. Returns
`{paper_id, status:"summarization_queued", message, force}`.

### 11.11 `POST /papers/{paper_id}/reconstruct-reading-order` → `202`

Dispatches `reconstruct_reading_order` — sends chunks + bounding boxes to
the resolved chat model (§15) to produce the canonical reading order for
two-column / complex papers.

### 11.12 `GET /papers/{paper_id}/chunks?limit=100&offset=0`

`{chunks: [...], paper_id, total}`.

### 11.13 `GET /papers/{paper_id}/chunks/{sequence_order}`

```json
{
  "id":               "<uuid>",
  "paper_id":         "<uuid>",
  "sequence_order":   <int>,
  "content_markdown": "...",
  "structural_type":  "heading|text|math|table|figure|...",
  "plain_text":       "...",
  "page_start":       <int|null>,
  "page_end":         <int|null>,
  "heading_path":     ["...", ...] | null,
  "image_url":        "/static/images/<doc_id>/<uuid>.png" | null,
  "image_refs":       ["<original_name>", ...]
}
```

`404 ChunkNotFound` when there's no chunk at `sequence_order` — this is the
"end of paper" signal for the reading view.

### 11.14 `GET /papers/{paper_id}/figure-descriptions`

`{descriptions: FigureDescription[]}` — rich VLM-generated technical
descriptions per figure (see §9.9).

### 11.15 `POST /papers/{paper_id}/ask`

Body (`AskPayload`):

```json
{
  "query":                    "What is shown in this figure?",
  "current_sequence_order":    3,
  "conversation_id":           "<uuid>",
  "visible_sequence_orders":   [3, 4, 5],
  "focused_element":           "figure:7" | "table:3" | "architecture-diagram" | null,
  "images_b64":                ["<raw base64, no data: prefix>", ...]
}
```

Response (`AskResponse`):

```json
{
  "answer":              "...",
  "context_type":        "LOCAL|GLOBAL|OVERVIEW|EXTERNAL|OUT_OF_SCOPE",
  "router_reason":       "...",
  "citations":           [Citation, ...],
  "model":               "gemma4:26b",
  "conversation_id":     "<uuid>",
  "research_performed":  true|false,
  "research_summary":    "Studied N sources across M iterations" | null
}
```

`Citation`:

```ts
{
  chunk_id?:    string,
  sequence_id?: number,
  page?:        number,
  text_snippet?:string,
  url?:         string,       // for web citations
  source?:      "document" | "<engine name>"
}
```

### 11.16 `GET /papers/{paper_id}/chat?conversation_id=<uuid>`

Returns saved turns (oldest first) optionally restricted to one conversation.

### 11.17 `GET /papers/{paper_id}/conversations`

Returns every distinct conversation thread for a paper, newest first:

```json
{ "conversations": [
    { "conversation_id":"<uuid>", "turn_count":N, "started_at":"...", "last_at":"...", "first_user_message":"..." }, ...
]}
```

### 11.18 `GET /search/vector?q=...&document_id=<uuid?>&limit=10`

Direct pgvector search (debug). `{results, query, total}`.

### 11.19 `GET /search/web?q=...&limit=5`

Direct SearXNG search (debug). `{results, query, total}`.

### 11.20 Static mounts (no `/api/v1` prefix)

- `GET /static/images/<doc_id>/<file>`
- `GET /static/extracted/<doc_id>/...`
- `GET /static/assets/<doc_id>.pdf`
- `GET /static/images/research/<conv_id>/<file>`

### 11.21 Errors

| Exception          | HTTP | Body                                          |
| ------------------ | ---- | --------------------------------------------- |
| `DocumentNotFound` | 404  | `{"detail":"Document <id> not found"}`        |
| `ChunkNotFound`    | 404  | `{"detail":"No chunk at sequence_order=N"}`   |
| Body too large     | 413  | `{"detail":"File too large (X MB)..."}`       |
| Internal failure   | 500  | `{"detail":"<message>\n\n<traceback>"}`        |

---

## 12. Ingestion pipeline (step by step)

```
[Client]               [API]                          [Celery worker]
─────────              ─────                          ────────────────
drop PDF
   │
   ▼
POST /papers/upload    1. write documents/<uuid>.pdf
                       2. insert documents (queued)
                       3. write assets/<doc_id>.pdf
                       4. insert ingestion_jobs (queued)
                       5. process_ingestion.delay(...)
                       ◄── 201 {id, status:'processing'}
   │
   ▼
poll /progress every 1s                                run_pipeline_sync()
                                                       ├─ job → 'extracting'
                                                       ├─ mineru -p ... -o extracted/<id>
                                                       │   (or PyMuPDF fallback if allowed)
                                                       ├─ job → 'chunking'
                                                       │   parse content_list.json → structural chunks
                                                       │   (math, table, figure, heading detection)
                                                       ├─ move images → images/<id>/
                                                       │   insert chunk_assets
                                                       ├─ insert chunks
                                                       ├─ job → 'embedding'
                                                       │   embed_document.delay(doc_id)
                                                       ├─ documents.page_count via pypdf
                                                       ├─ job → 'complete'
                                                       └─ documents → 'complete'
   │
   ▼                                                   embed_document_chunks_sync()
status='complete'                                       ├─ batches of 20 chunks → ollama /api/embeddings
   │                                                   ├─ insert chunk_embeddings (vector(VECTOR_DIMENSION))
   ▼                                                   └─ on completion → generate_section_summaries.delay()
ReadingView opens
                                                       generate_section_summaries (high-quality)
                                                       ├─ hierarchical summaries → section_summaries
                                                       └─ VLM figure descriptions → figure_descriptions
```

Key files:

- `app/api/v1/endpoints/documents.py::upload_paper` — entry.
- `app/workers/tasks.py::process_ingestion` — Celery wrapper.
- `app/extraction/pipeline_sync.py::run_pipeline_sync` — sync orchestration.
- `app/extraction/mineru_client.py` — subprocess wrapper around `mineru`.
- `app/extraction/chunker.py` — markdown / content_list → structural chunks.
- `app/extraction/assets.py::move_asset_to_storage` — copies images with
  randomized filenames into `images/<doc_id>/<uuid>.ext`.
- `app/embeddings/service_sync.py::embed_document_chunks_sync` — batched
  embedding via Ollama `/api/embeddings`.

### Status state machine

| `ingestion_jobs.status` | `documents.status` | Frontend overlay        |
| ----------------------- | ------------------ | ----------------------- |
| `queued`                | `queued`           | "Queued"                |
| `extracting`            | `queued`           | "Extracting"            |
| `chunking`              | `queued`           | "Chunking"              |
| `embedding`             | `queued`           | "Embedding"             |
| `summarizing`           | `complete`         | overlay closes, summarization continues in background |
| `complete`              | `complete`         | flip to ReadingView     |
| `failed`                | `failed`           | back to library + error |

### Re-chunking & re-extraction

- `POST /papers/{id}/rechunk` — re-run chunker on cached MinerU output (cheap).
- `POST /papers/{id}/reextract` — wipe & re-run MinerU + chunker (full).
- `POST /papers/{id}/regenerate-summaries` — re-run summaries + VLM
  descriptions (slow; minutes per paper).
- `POST /papers/{id}/reconstruct-reading-order` — fix two-column papers.

---

## 13. Chat orchestration & /ask flow

Entry: `app/chat/orchestrator.py::handle_ask`. Steps (see `ASK[stepN]` log
lines for live telemetry):

### Step 0.5 — Topic guardrail (`chat/guardrail.py`)

Quick LLM classification that returns one of `ALLOWED` / `OUT_OF_SCOPE`. The
guardrail prompt allows anything in IT / CS / software / AI / data /
networking / cybersecurity / cloud / devops / programming and *applications of
IT inside other sectors* ("how is AI used in healthcare"). Pure non-IT
questions are rejected with `"This is out of scope."` and the turn is logged
with `context_type='OUT_OF_SCOPE'`. When the user is inside a paper
(`in_paper_context=True`), generic prompts like "describe this figure" are
treated as in-scope.

### Step 1 — Router (`chat/router.py::route_prompt`)

Returns `RouterDecision(context_type, reason, confidence)`.

1. **Heuristics first** — keyword lists:
   - `_LOCAL_KEYWORDS` (`"this figure"`, `"above"`, `"this equation"`, …) —
     only fires when a `current_chunk_id` exists.
   - `_OVERVIEW_KEYWORDS` (`"summarize the paper"`, `"main contribution"`,
     `"tl;dr"`, `"executive summary"`, …) — fires for paper-level questions.
   - `_EXTERNAL_KEYWORDS` (`"latest"`, `"2025"`, `"who is"`, `"wikipedia"`, …).
2. **No-document fallback**: if no chunk and no document → EXTERNAL.
3. **LLM fallback** for ambiguity: uses `ROUTER_SYSTEM_PROMPT`, low
   temperature, parses first token (`LOCAL` / `GLOBAL` / `OVERVIEW` / `EXTERNAL`).
4. **Default** when ambiguous + document present → GLOBAL.

### Step 2 — Context retrieval

Per route:

- **LOCAL** (`chat/local_context.py`) — fetches a window of `settings.local_context_window` chunks
  on each side of the current one, plus every `chunk_assets` row in that window
  (only images are surfaced to the multimodal model).
- **GLOBAL** (`chat/global_context.py`) — `services/retrieval.search_chunks`
  (embed query → pgvector cosine search ordered by `<=>`) with `limit=3`.
  Also surfaces every image attached to retrieved chunks so the model can
  embed them inline with `![caption](url)`.
- **OVERVIEW** (`chat/overview_context.py`) — bypasses vector search;
  fetches all `section_summaries` rows for the document (executive + H1 + H2),
  formats them as a structured outline. Citations come from each summary's
  `source_chunk_ids`.
- **EXTERNAL** (`chat/external_context.py`) — `search/searxng_client.search`
  → `search/ranking.rank_results`, top-5 by default.

### Step 2b — Web pre-fetch policy

Conditional web pre-fetch (see `_user_explicitly_mentioned_other_field` in
`orchestrator.py`):

```python
do_web_prefetch = (
    decision.context_type == "EXTERNAL"
    or cross_field_explicit
    or not has_paper_context
)
```

Otherwise the web is left out so paper context isn't polluted with off-domain
noise.

### Step 3 — Multimodal message build (`llm/multimodal.py`)

```python
messages = [
  {"role":"system", "content": <prompt>},
  {"role":"user",   "content": "Context:\n<context_text>\n\n<original prompt>",
                    "images":  ["<base64 PNG/JPEG>", ...]},  # only when present
]
```

`COMBINED_SYSTEM_PROMPT` is used unless the path is "research-aware"
(EXTERNAL / cross-field / weak paper context), in which case
`RESEARCH_AWARE_COMBINED_PROMPT` is used. The latter allows the model to emit
a `NEEDS_RESEARCH` signal that triggers the research agent.

### Step 4 — LLM call (`llm/client.py` → resolver → backend)

Call sites use the backend-agnostic entry points in `llm/client.py`
(`chat`, `stream_chat`, `chat_sync`) and pass a **role** (`"chat"`,
`"classifier"`, `"vlm"`) instead of a model name. The client asks
`llm/resolver.py` for the active target (see §15), picks the model for that
role, then dispatches: Ollama targets go through `llm/ollama_client.py`
(POST `{base}/api/chat`), cloud targets POST `{base}/chat/completions` with
the OpenAI-compatible schema and a Bearer key. **`httpx.Timeout`** is set to
`connect=10s, read=600s, write=10s, pool=10s` — large `read` is intentional so
a big local model doesn't blow up at 120 s.

### Step 4.5 — Research agent (`chat/research_agent.py`)

When `RESEARCH_AWARE_COMBINED_PROMPT` is active *and* the first-pass answer
contains a `NEEDS_RESEARCH` block, `run_research_agent` runs an iterative
Observe → Reason → Act → Interpret loop with tools:

- `web_search` — SearXNG.
- `read_paper_section` — fetch chunks by sequence / heading.
- `describe_figure` — VLM call.

Persists images it deems useful under `images/research/<conversation_id>/...`
and returns `{findings_markdown, sources, local_images, iterations}`. A
**second** synthesis pass feeds findings back into the same model. Sources
are merged into citations; remote image URLs in the final answer are
rewritten to the persisted local URLs (`_rewrite_research_image_urls`).

### Step 5 — Citation hygiene

`_filter_unused_web_citations` drops web citations whose URL never appears in
the final answer body. Chunk citations (no URL) are always kept.

### Step 6 — Persistence + tracing

- `conversation_turns` row for the user turn.
- `conversation_turns` row for the assistant turn (with `context_type`,
  `router_reason`, `model`, JSON `citations`).
- `ask_traces` row with `prompt_tokens`, `completion_tokens`, `latency_ms`.

### Step 7 — Compaction

`maybe_compact_conversation` counts user turns since the last `compaction`
turn; when ≥ `COMPACTION_THRESHOLD=5` it asks the LLM to produce a dense
summary and inserts it as a `role='compaction'` turn. Subsequent
`format_conversation_history` calls use the compaction summary instead of the
raw history.

---

## 14. Domain guardrail & cross-field policy

The product is **strictly a CS / ML / AI / systems / engineering assistant by
default**. This is enforced at four layers:

1. **`is_topic_allowed` guardrail** (§13 step 0.5) — pure non-IT questions
   are rejected before the LLM is ever invoked.
2. **`DOMAIN_PREAMBLE`** in `chat/prompts.py` — the model is instructed to
   *never* fall back to the biology, medical, chemistry, physics, finance,
   linguistics, or everyday-English meaning of a term unless the user's
   prompt explicitly names that other field. Off-domain web hits must be
   silently ignored (no "Note: I ignored biology results" sentences). The
   model is forbidden from writing a trailing `Sources: None.` line.
3. **`_user_explicitly_mentioned_other_field`** in `chat/orchestrator.py` —
   detects whether the user named a non-CS field (full list in code:
   biology, biomedical, genetics, medicine, clinical, chemistry, physics,
   neuroscience, linguistics, economics, finance, law, sociology,
   anthropology, philosophy, history, music, art) or used a cross-field
   trigger phrase (`"in other fields"`, `"applied to "`, `"applications in "`,
   …). Only then does the orchestrator allow web pre-fetch and the
   research-aware prompt.
4. **`_filter_unused_web_citations`** — even if a web citation slips through,
   it's stripped from the chip row when its URL doesn't appear in the answer.

Expected behavior:

| Prompt                                          | Default route + behavior                              |
| ----------------------------------------------- | ----------------------------------------------------- |
| `"What is transduction?"`                       | No web pre-fetch; CS-only answer from paper context.  |
| `"Also bring a picture of it"`                  | Embeds the paper figure; no web noise.                |
| `"How is transduction used in biology?"`        | Cross-field trigger → web allowed; bridges to biology.|
| `"How does X apply to genetics?"`               | Cross-field trigger → research path enabled.          |
| Any pure non-IT question (e.g. medical advice)  | Guardrail rejects → `"This is out of scope."`         |

---

## 15. AI backend & models (auto-detection, chat, VLM, embedding)

### How the backend is chosen (`app/llm/resolver.py`)

Nothing in the codebase hardcodes a model or provider. With the default
`LLM_PROVIDER=auto` the resolver decides per call (probe result cached 30 s):

1. **Ollama reachable** at `OLLAMA_BASE_URL` (GET `/api/tags`, 3 s timeout)
   → use Ollama with `CHAT_MODEL` / `VLM_MODEL` / `CLASSIFIER_MODEL`.
2. **Else walk the cloud keys in order** — OpenAI → Anthropic → Gemini →
   xAI → DeepSeek — and use the first `*_API_KEY` that is set, speaking the
   OpenAI-compatible `/chat/completions` protocol.
3. **Else raise `NoLLMConfigured`** (HTTP 503, code `NO_LLM_CONFIGURED`) with
   verbatim instructions: *"No AI backend is configured. Put your API key or
   your Ollama connection in backend/.env…"* — shown in chat errors, API
   responses, and the startup log.

Pin a backend explicitly with `LLM_PROVIDER=ollama|openai|anthropic|gemini|
xai|deepseek|custom` (`custom` = any OpenAI-compatible endpoint via
`LLM_BASE_URL`, key optional).

**Two model namespaces.** `CHAT_MODEL` / `VLM_MODEL` / `CLASSIFIER_MODEL` /
`EMBEDDING_MODEL` are reserved for Ollama (and `custom`) — an Ollama tag like
`gemma4:31b-cloud` is never sent to a cloud API. Each cloud provider has its
own setting with a sensible default: `OPENAI_CHAT_MODEL=gpt-4o`,
`ANTHROPIC_CHAT_MODEL=claude-sonnet-4-6`, `GEMINI_CHAT_MODEL=gemini-2.5-flash`,
`XAI_CHAT_MODEL=grok-4`, `DEEPSEEK_CHAT_MODEL=deepseek-chat` (no vision —
figure images can't be described), `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`,
`GEMINI_EMBEDDING_MODEL=gemini-embedding-001`.

**Embeddings** follow the same chain but only OpenAI and Gemini offer
embedding APIs. The choice is **pinned per process** after the first
successful resolution — vectors from different models are not comparable, so
a mid-run Ollama hiccup must never mix models inside one library. Startup
detects a stored-vs-active model mismatch (§6.5 step 9): pinned provider →
automatic wipe + re-embed; auto → loud warning only.

### Roles

| Role           | Resolved model (Ollama / cloud)                        | Notes                                                  |
| -------------- | ------------------------------------------------------ | ------------------------------------------------------ |
| Chat           | `CHAT_MODEL` / provider's `*_CHAT_MODEL`               | Multimodal — receives base64 images alongside text.    |
| VLM            | `VLM_MODEL` (empty = chat) / provider chat model       | Used by `figure_describer_sync`.                       |
| Classifier     | `CLASSIFIER_MODEL` (empty = chat) / provider chat model| Router + guardrail; a small fast model is the biggest `/ask` speedup. |
| Embedding      | `EMBEDDING_MODEL` / `*_EMBEDDING_MODEL`                | Stored at `VECTOR_DIMENSION` (default **1024**); larger outputs truncated+renormalized (MRL), smaller zero-padded. |
| Reading order  | (chat role)                                            | Long-context call; can be slow on large papers.        |
| Section summary| (chat role, resolved upfront — name keys idempotency)  | Multi-pass; expensive on local models.                 |

The httpx client uses a 600-second read timeout to accommodate cold-start
+ large model inference.

---

## 16. Frontend application reference

Vite 6 + React 19 + Tailwind 3, single-page app with hash-based routing.

### 16.1 Routing (`App.tsx`)

`Route = 'library' | 'processing' | 'reading' | 'pdf-viewer'`. Synced to
`window.location.hash` (`#/library`, `#/paper/<id>`, `#/raw/<id>`) so refreshes
restore state. On mount, parses the hash and fetches the relevant paper.

### 16.2 Fetch client (`api.ts`)

Typed wrappers around every endpoint listed in §11 — `listPapers`,
`uploadPaper`, `getPaper`, `getPaperProgress`, `getChunk`, `getChunkCount`,
`deletePaper`, `askPaper`, `getPaperChat`, `listPaperConversations`,
`getFigureDescriptions`, `reextractPaper`, `triggerReadingOrderReconstruction`,
`checkHealth`, `getRawPdfUrl`, `getStaticPdfUrl`. All requests go through
`/api/v1` and are proxied by Vite to `http://localhost:8000`.

### 16.3 Views

- **`LibraryView`** — grid/list of papers, real-time polling, drag-drop
  upload, search, sort (`recent → title → pages`), layout toggle. Falls back
  to the static `LIBRARY` from `data.ts` when the API is unreachable.
- **`ProcessingOverlay`** — animated overlay shown during ingestion. Surfaces
  fine-grained `job_status` (extracting / chunking / embedding), extractor
  badge (`mineru` vs `pymupdf_fallback`), and error messages. Does **not**
  auto-close on completion — the user clicks "Back to library".
- **`ReadingView`** — split pane:
  - **Reader (left)** — fetches chunks one at a time via
    `GET /papers/{id}/chunks/{seq}`. "Reveal next" advances `nextSequence`;
    triggered by clicking the next button or holding **D** + pressing **↓**.
    A `404` sets `atEnd=true`. Each chunk dims when superseded by a newer
    reveal. Renders: `heading` (serif), `figure` (`<img>` + caption),
    `math` (KaTeX), `table` (mono), default (paragraph with KaTeX inline).
  - **`ChatPane` (right)** — local turn log; `send()` calls
    `askPaper(paperId, q, currentSequenceOrder, conversationId,
    {visibleSequenceOrders, focusedElement, imagesB64})`. Citations are
    reduced to compact chips beneath each assistant turn — clicking a chunk
    chip jumps the reader to that sequence; clicking a web chip opens the
    URL. Defensive `stripTrailingSourcesNone` removes any literal
    `"Sources: None."` line that slips through.
- **`PdfViewer`** — full-screen `react-pdf` renderer for the raw upload.
- **`RawFilesPanel`** — slide-over listing every uploaded PDF; opens the
  PDF viewer.

### 16.4 Styling

Design tokens (`--bg`, `--bg-2`, `--bg-3`, `--fg`, `--muted`, `--accent`,
`--ok`, `--border`) in `src/index.css`. Newsreader / Inter / JetBrains Mono
fonts. Tailwind utilities everywhere; `style={{...}}` for token references.
A `@media (prefers-color-scheme: dark)` block re-binds the tokens for dark
mode.

---

## 17. Background workers (Celery)

App: `app.core.celery_app:celery_app`. Broker = result backend = Redis.

```bash
celery -A app.core.celery_app worker --loglevel=info
```

Configured with: `task_serializer=json`, `task_acks_late=True`,
`worker_prefetch_multiplier=1`, `result_expires=24h`.

### Tasks (`app/workers/tasks.py`)

| Name                                | Trigger                                                    | What it does                                                         |
| ----------------------------------- | ---------------------------------------------------------- | -------------------------------------------------------------------- |
| `9xaipal.process_ingestion`         | `POST /papers/upload`, `/reextract`                        | Runs `run_pipeline_sync` (MinerU + chunker + asset linking).         |
| `9xaipal.embed_document`            | After ingestion (auto)                                     | Batches chunks → Ollama embedding → `chunk_embeddings`. On success, chains `generate_section_summaries`. Retries up to 3× with 10s backoff. |
| `9xaipal.generate_section_summaries`| After embedding (auto), `POST /regenerate-summaries`       | Builds `section_summaries` (executive + H1 + H2). Also calls `generate_figure_descriptions_sync` for VLM descriptions. |
| `9xaipal.reconstruct_reading_order` | `POST /reconstruct-reading-order`                          | LLM-based reading order fix (two-column papers).                     |

Every task disposes the sync engine first (`sync_engine.dispose()`) so each
forked worker process gets a fresh connection pool — critical for
`prefork`-based Celery.

---

## 18. Logging & tracing

- **Application logs** — `app.core.logging.get_logger(__name__)`. All
  `/ask` steps emit `ASK[stepN]` markers; ingestion logs use `[celery]` and
  `[pipeline]` prefixes.
- **`ask_traces`** — per-call row with `prompt_tokens`, `completion_tokens`,
  `latency_ms`, `context_type`, `router_reason`, `model`. Query directly:
  ```sql
  SELECT created_at, context_type, model, latency_ms,
         prompt_tokens, completion_tokens
  FROM ask_traces
  ORDER BY created_at DESC
  LIMIT 50;
  ```
- **`conversation_turns`** — full chat log including `router_reason` and
  `citations` JSONB.
- **`ingestion_jobs`** — pipeline state machine; `started_at` / `completed_at`
  bracket each phase.

---

## 19. Failure modes & recovery

| Failure                                  | Symptom                                                       | Recovery                                                                                   |
| ---------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Postgres unreachable                     | `/health` → `database:"unavailable"`, requests 5xx            | Start Postgres; lifespan does not crash so the API will recover on the next request. In compose: `restart: unless-stopped` + autoheal restart it automatically. |
| No AI backend at all (Ollama down, no keys) | 503 `NO_LLM_CONFIGURED`; chat shows "No AI backend is configured. Put your API key or your Ollama connection in backend/.env…" | Start Ollama **or** paste any one cloud key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`) and restart. |
| Ollama down (cloud key present)          | Nothing visible — `LLM_PROVIDER=auto` falls back to the first cloud key within ~30 s (probe TTL) | No action needed; restart Ollama to shift back to local.                                   |
| Ollama model not pulled                  | First request hangs while model downloads                     | `ollama pull <CHAT_MODEL>` ahead of time.                                                   |
| Embedding model switched (stored ≠ active) | Startup warning; degraded vector search (vectors not comparable) | Pin `EMBEDDING_PROVIDER` and restart → stale vectors wiped + library re-embedded automatically. Or restore the old backend. |
| Container crashes / worker OOM (exit 137) | `docker ps` shows restart; uploads pause then resume          | Automatic — `restart: unless-stopped` on every long-running service. Lower `MINERU_PAGE_BATCH_SIZE` or raise `WORKER_MEM_LIMIT` if OOM repeats. |
| Container hung (running but unresponsive) | Healthcheck shows `unhealthy` in `docker ps`                  | Automatic — the `autoheal` watchdog restarts unhealthy containers labeled `autoheal=true` (api, postgres, redis). |
| SearXNG down                             | EXTERNAL branch returns empty; answer is ungrounded but works | Start SearXNG (`docker compose up -d searxng`).                                            |
| MinerU not installed                     | Pipeline marks doc `failed` with `MinerUError`                | Install MinerU; or set `ALLOW_PYMUPDF_FALLBACK=true` for degraded mode (no OCR/tables/math).|
| Redis down (Celery)                      | Upload returns `failed` with descriptive `error_message`      | Start Redis + worker.                                                                       |
| Worker crashes mid-ingestion             | Document stuck in `extracting`/`chunking`/`embedding`         | `POST /papers/{id}/reextract` or manually update `documents.status='failed'`.              |
| 2-min `/api/chat` timeout                | HTTP 500 on chat at exactly 120 s                             | Fixed by `httpx.Timeout(read=600)`; ensure that change is deployed.                        |
| Empty paper context drift                | Answers wander to non-CS domains                              | Guardrail + DOMAIN_PREAMBLE + cross-field gate (see §14).                                  |
| Garbage citation chips (off-domain URLs) | Chips show MDN / dictionary results                           | `_filter_unused_web_citations` drops them.                                                  |
| `Sources: None.` rendered                | Trailing literal line in answer                                | Stripped server-side via DOMAIN_PREAMBLE; defensive client strip in `ChatPane`.            |
| Frontend stuck on processing overlay     | Backend up but no progress                                     | Check Celery worker logs; check `ingestion_jobs` row directly.                              |

---

## 20. Security notes

- **Local-first by design.** Nothing leaves the host except SearXNG queries
  (when activated by the EXTERNAL route or explicit cross-field prompts).
- **`backend/.env` is gitignored**, but the in-repo `.env` currently carries a
  development HF token (`HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN`). **Rotate it
  at https://huggingface.co/settings/tokens before production.**
- **No authentication** — the API is open to any caller that can reach
  `:8000`. Production deployments must front it with an auth proxy.
- **CORS** allows `localhost:5173`, `localhost:3000`, `127.0.0.1:5173` only.
  Adjust `app/main.py` for any other origin.
- **File uploads** are capped at `MAX_UPLOAD_SIZE_MB` (default 100 MB).
- **Static mounts** expose every file in `storage/` under `/static/*`. Treat
  uploaded PDFs and extracted assets as **not** containing secrets.
- **PostgreSQL password** defaults to `9xaipal_dev_password`. Rotate via env.
- **MinerU** spawns subprocesses; the binary must be trusted (it's pulled
  from upstream).

---

## 21. Production test plan (manual + automated)

### 21.1 Automated tests (`backend/tests/`)

Run with:

```bash
cd backend
pip install -e .[dev]
pytest -v
```

Suites:

| File                          | Coverage                                                     |
| ----------------------------- | ------------------------------------------------------------ |
| `test_chunk_sequence.py`      | Chunker sequence numbering + structural type detection.      |
| `test_context_router.py`      | LOCAL / GLOBAL / OVERVIEW / EXTERNAL routing decisions.       |
| `test_ingestion_pipeline.py`  | End-to-end pipeline (MinerU/PyMuPDF stub → chunks → assets). |
| `test_vector_retrieval.py`    | `search_chunks` against pgvector with deterministic vectors. |
| `test_provider_resolver.py`   | AI backend auto-detection (§15): Ollama-first, cloud fallback order, per-provider models (Ollama tags never sent to clouds), `NoLLMConfigured` instructions, embedding pinning + chat-only providers skipped. |

`conftest.py` wires an in-memory async session against a test database (set
`DATABASE_URL` to point at a throwaway DB before running).

### 21.2 Manual smoke (acceptance)

1. **Health** — `GET /api/v1/health` returns all `ok`.
2. **Upload (MinerU path)** — drop a real paper (e.g. "Attention Is All You
   Need" — included at `docs/Attention Is All You Need.pdf`). Confirm:
   - Overlay shows `extracting → chunking → embedding`.
   - On completion the reading view renders heading + first paragraph.
3. **Reveal-next** — press the "next" button and the **D + ↓** chord. Confirm
   chunks reveal one at a time, math renders via KaTeX, figures show the
   correct image.
4. **Figure VLM** — wait ~5–15 min for `generate_section_summaries` to
   complete; `GET /papers/{id}/figure-descriptions` returns non-empty rows.
5. **Chat LOCAL** — ask `"What does this figure show?"` while a figure chunk
   is current. Confirm:
   - `context_type=LOCAL`, `router_reason` mentions "matched: 'this figure'".
   - Answer references the actual diagram (multimodal worked).
6. **Chat GLOBAL** — ask `"What is the encoder-decoder attention mechanism?"`.
   Confirm `context_type=GLOBAL`, citations point to the right chunks.
7. **Chat OVERVIEW** — ask `"Summarize the paper"`. Confirm
   `context_type=OVERVIEW`, answer references multiple sections.
8. **Chat EXTERNAL** — ask `"What is the latest news on transformer models?"`.
   Confirm `context_type=EXTERNAL`, citations include web URLs.
9. **Domain guardrail** — ask `"What's the best treatment for migraines?"`.
   Confirm reply is `"This is out of scope."` (logged with
   `context_type=OUT_OF_SCOPE`).
10. **Cross-field bridge** — ask `"How is attention used in neuroscience?"`.
    Confirm web pre-fetch fires and the answer bridges to neuroscience.
11. **Default CS-only** — ask `"What is transduction?"`. Confirm answer is
    sequence-transduction (CS), with the Transformer figure and no biology.
12. **Citation chips** — confirm web citations whose URL is not actually
    used in the answer body do **not** render chips.
13. **Conversation continuity** — send 6+ user turns. Confirm:
    - `conversation_id` is preserved.
    - After ~5 turns, a `compaction` row appears in `conversation_turns`.
    - History injection keeps the model coherent.
14. **`/conversations`** — `GET /papers/{id}/conversations` lists every
    thread; opening one via `/chat?conversation_id=…` loads turns.
15. **Reading order reconstruction** — for a two-column paper, hit
    `POST /papers/{id}/reconstruct-reading-order`. After completion the
    `documents.reading_order` JSONB is populated.
16. **Re-chunk** — `POST /papers/{id}/rechunk`. Confirm chunks are rebuilt
    and embeddings re-queued.
17. **Re-extract** — `POST /papers/{id}/reextract`. Confirm MinerU runs again
    and the document re-enters the processing overlay.
18. **Delete** — `DELETE /papers/{id}`. Confirm:
    - `204` returned.
    - DB rows in `documents`, `chunks`, `chunk_embeddings`, `chunk_assets`,
      `figure_descriptions`, `section_summaries`, `ingestion_jobs` are gone.
    - On-disk files in `documents/`, `assets/`, `extracted/`, `images/` are
      removed (best-effort; missing files are tolerated).
19. **Refresh persistence** — open `#/paper/<id>` and refresh; reading view
    is restored from the URL hash.
20. **Failure surfacing** — stop Ollama, ask a question; the chat shows a
    polite error. Restart Ollama; the next ask succeeds.

### 21.3 Performance baselines (target machine: M-series Mac, 32 GB)

| Operation                                          | Expected         |
| -------------------------------------------------- | ---------------- |
| Upload + ingestion (10-page paper, MinerU)         | 1–2 min          |
| Embedding (10-page paper, ~80 chunks)              | 30–60 s          |
| Section summarization (10-page paper)              | 5–15 min         |
| `/ask` LOCAL (1 image)                             | 8–30 s           |
| `/ask` GLOBAL (top-3 chunks)                       | 5–20 s           |
| `/ask` OVERVIEW                                    | 5–15 s           |
| `/ask` EXTERNAL with research agent (3 iterations) | 30–120 s         |

Tune `LOCAL_CONTEXT_WINDOW`, `limit` in `build_global_context`, and the
Celery `worker_concurrency` for your hardware.

---

## 22. Operations playbook

### Tail logs

```bash
# Backend
uvicorn app.main:app --reload --port 8000 --log-level info

# Celery
celery -A app.core.celery_app worker --loglevel=info
```

### Inspect ask traces

```sql
SELECT created_at, context_type, model, latency_ms, prompt_tokens, completion_tokens
FROM ask_traces ORDER BY created_at DESC LIMIT 20;
```

### Inspect conversations

```sql
SELECT conversation_id, COUNT(*) AS turns,
       MIN(created_at) AS started, MAX(created_at) AS last
FROM conversation_turns
WHERE document_id = '<uuid>'
GROUP BY conversation_id
ORDER BY last DESC;
```

### Wipe and reset a paper

```sql
DELETE FROM documents WHERE id = '<uuid>';
-- cascade removes chunks, embeddings, assets, summaries, figure_descriptions,
-- ingestion_jobs; conversation_turns retain history with document_id=NULL.
```

(Pair this with `rm -rf storage/{documents,assets,extracted,images}/*<id>*`
for full disk cleanup if `DELETE /papers/{id}` was bypassed.)

### Force re-embed all chunks

```sql
DELETE FROM chunk_embeddings
WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id='<uuid>');
```

Then `POST /papers/{id}/rechunk` (which re-queues `embed_document`).

### Inspect pipeline state

```sql
SELECT j.status, j.started_at, j.completed_at, d.status AS doc_status,
       d.extractor, d.error_message
FROM ingestion_jobs j JOIN documents d ON d.id = j.document_id
ORDER BY j.created_at DESC LIMIT 20;
```

### Scale workers

```bash
celery -A app.core.celery_app worker --loglevel=info --concurrency=4 -n w1@%h
celery -A app.core.celery_app worker --loglevel=info --concurrency=4 -n w2@%h
```

---

## 23. Known gaps & future work

- **No authentication** on the HTTP API.
- **`DELETE /papers/{id}`** is best-effort for disk cleanup; missing files
  are tolerated but orphans aren't garbage-collected on schedule.
- **`page_start` / `page_end`** on `chunks` are nullable and currently not
  populated (MinerU page metadata not wired).
- **`chunk_assets.caption` / `width` / `height`** are reserved fields.
- **Research images** persist under `images/research/<conversation_id>/`; no
  cleanup task exists yet.
- **`section_summaries`** generation is single-pass per `(document, prompt
  template)`; long papers may exceed the model's effective context.
- **No multi-tenant isolation** — all data is shared in one DB.
- **Cross-paper search** (GLOBAL across the entire library) is not yet
  surfaced in the API; `search_chunks` already supports `document_id=None`
  but the chat orchestrator does not invoke it.
- **No retry queue** for failed ingestions beyond the in-Celery
  `embed_document` retries.
- **Web search images** beyond research-agent context are not persisted —
  remote URLs in chat answers may rot.

---

### Pointer documents

- `docs/setup.md` — original setup quick-start (subset of §6).
- `docs/architecture.md` — architecture summary (subset of §3 + §8).
- `docs/ingestion-pipeline.md` — pipeline (subset of §12).
- `docs/chat-and-ask.md` — chat orchestration (subset of §13).
- `docs/api-reference.md` — API (subset of §11).
- `docs/database-schema.md` — schema (subset of §9).
- `docs/frontend.md` — frontend (subset of §16).
- `docs/storage-and-static-files.md` — storage layout (subset of §10).
- `docs/Architecture.html` — visual interactive companion to
  `Architecture_Technical.md`.
- `docs/Architecture_Technical.md` — deeper architectural notes.
- `backend/docs/MIGRATIONS.md` — schema evolution notes.

If any section here drifts from the code, the **code is authoritative** —
file paths and module names in this document point you straight to the
source of truth.
