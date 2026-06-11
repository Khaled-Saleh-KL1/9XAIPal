# Setup & Running

## Required services

| Service        | Default URL              | Purpose                                 |
| -------------- | ------------------------ | --------------------------------------- |
| PostgreSQL 15+ | `localhost:5432`         | Documents, chunks, embeddings, chat log |
| `pgvector`     | extension in the DB      | Cosine-similarity search over chunks    |
| Ollama         | `localhost:11434`        | Chat + VLM (`chat_model`, `vlm_model`)  |
| Ollama         | `localhost:11434`        | Embedding model (`embedding_model`)     |
| SearXNG        | `localhost:8080`         | External web search (EXTERNAL context)  |
| MinerU CLI     | binary on `$PATH`        | PDF → structured markdown + images      |

All of these run locally. The defaults are wired in
[backend/app/core/config.py](../backend/app/core/config.py); override via a
`.env` file in `backend/` (template: `backend/.example.env`).

**Ollama is optional.** With `LLM_PROVIDER=auto` (default) the backend uses
Ollama when it is reachable and otherwise falls back to the first cloud API
key set in `.env`, in order: `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` →
`GEMINI_API_KEY` → `XAI_API_KEY` → `DEEPSEEK_API_KEY`. If neither is
available, every request answers with clear instructions to add one. Full
details: [README §15](README.md#15-ai-backend--models-auto-detection-chat-vlm-embedding).

Ollama models are configured via env vars:
- `CHAT_MODEL` — chat (e.g. `gemma4:31b-cloud`)
- `VLM_MODEL` — figure describer pipeline (empty = reuse `CHAT_MODEL`)
- `CLASSIFIER_MODEL` — router/guardrail (empty = reuse `CHAT_MODEL`)
- `EMBEDDING_MODEL` — embeddings (e.g. `qwen3-embedding`), stored at
  `VECTOR_DIMENSION` (default 1024)

Cloud providers have their own model settings (`OPENAI_CHAT_MODEL=gpt-4o`,
`ANTHROPIC_CHAT_MODEL=claude-sonnet-4-6`, `GEMINI_CHAT_MODEL=gemini-2.5-flash`,
`XAI_CHAT_MODEL=grok-4`, `DEEPSEEK_CHAT_MODEL=deepseek-chat`,
`OPENAI_EMBEDDING_MODEL=text-embedding-3-small`,
`GEMINI_EMBEDDING_MODEL=gemini-embedding-001`) — an Ollama tag is never sent
to a cloud API.

## Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
# Postgres must be reachable and the role/db created.
# Run the app — schema is applied at startup via app/database/migrations.py.
uvicorn app.main:app --reload --port 8000
```

At startup the FastAPI lifespan ([core/lifecycle.py](../backend/app/core/lifecycle.py)):

1. Configures logging.
2. Ensures every storage directory exists (`documents/`, `extracted/`,
   `images/`, `assets/`, `logs/`).
3. Verifies a database connection.
4. Applies migrations (creates pgvector + tables if missing).

Shutdown disposes the async engine.

Heavy work (PDF extraction, embedding, summarization) is handled by Celery
workers, not by BackgroundTasks:

```bash
# Separate terminal, same venv
celery -A app.core.celery_app worker --loglevel=info
```

## Frontend

```bash
cd frontend
npm install
npm run dev   # Vite on :5173, proxies /api and /static to :8000
```

The CORS middleware in [main.py](../backend/app/main.py) explicitly allows
`localhost:5173` (Vite), `localhost:3000`, and `127.0.0.1:5173`.

## docker-compose

[`backend/docker-compose.yml`](../backend/docker-compose.yml) brings up the
full stack:

| Service         | Image | Port | Purpose |
| --------------- | ----- | ---- | ------- |
| `postgres`      | `pgvector/pgvector:pg16` | 5432 | Database with pgvector |
| `redis`         | `redis:7-alpine` | 6379 | Celery broker + backend |
| `searxng`       | `searxng/searxng:latest` | 8080 | Local web search proxy |
| `celery_worker` | Built from `Dockerfile.mineru` | — | MinerU + embedding + summarization |
| `api`           | Built from `Dockerfile` | 8000 | FastAPI backend (+ serves the built UI) |
| `autoheal`      | `willfarrell/autoheal` | — | Restarts containers whose healthcheck turns unhealthy |

```bash
cd backend
docker compose up -d --build
```

All long-running services carry `restart: unless-stopped`, so a crashed or
OOM-killed container restarts automatically; the `autoheal` watchdog handles
the hung-but-running case. Data volumes are never touched.

To share the app with other devices on your local network, run
`backend/start-lan-server.sh` — it brings up the full stack (UI + API on one
port), prints the LAN URL, and tears everything down on Ctrl+C while keeping
your data ([README §6.6](README.md#66-temporary-lan-server-start-lan-serversh)).

For development, you can run only the ancillary services and keep the
backend on the host:

```bash
docker compose up -d postgres redis searxng
# Then run backend on host (see above)
# And celery worker on host:
celery -A app.core.celery_app worker --loglevel=info
```

## Verifying things work

| Endpoint                      | What "OK" looks like                                |
| ----------------------------- | --------------------------------------------------- |
| `GET /api/v1/health`          | `{status:"ok", database:"ok", ollama:"ok", …}`      |
| `POST /api/v1/papers/upload`  | `201` with `{id, status:"processing", …}`           |
| `GET /papers/{id}/progress`   | status transitions: `queued → extracting → chunking → embedding → summarizing → complete` |
| `GET /papers/{id}/chunks/1`   | returns the first structural chunk                  |
| `POST /papers/{id}/ask`       | returns `{answer, context_type, citations, …}`      |