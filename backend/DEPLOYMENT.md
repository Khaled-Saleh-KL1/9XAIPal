# Deployment: "My Computer Is the Server"

This document describes how to run the full 9XAIPal stack (API + UI + workers + infra) in containers so that **your machine acts as the server**.

Ollama (the LLM/VLM/embeddings) can stay on the host, or any cloud API can take over — with `LLM_PROVIDER=auto` (default) the backend uses Ollama when it is reachable and otherwise falls back to the first cloud API key found in `.env` (OpenAI → Anthropic → Gemini → xAI → DeepSeek). See "AI Backend" below. The rest of the stack is fully containerized and async-ready for multiple concurrent users on the same machine.

## Quick Start (Recommended for Your Machine as Server)

```bash
cd backend

# 1. Copy and review env (no secrets in the example)
cp .example.env .env
# Edit .env if you want different models, larger pools, etc.

# 2. (First time only — this builds the React UI once into a volume)
docker compose up frontend-build

# 3. Bring up the full server (postgres + redis + searxng + api on :8000)
# The api service serves BOTH the React UI and the /api/v1 backend on a single port.
docker compose up -d api

# 4. (Optional but recommended for heavy ingestion)
docker compose up -d celery_worker
```

Then open **http://localhost:8000** in your browser. Everything (library, reader, chat, sub-threads, research images, etc.) is served from the container.

## Key Pieces Now Containerized

- **api**: FastAPI (async, 2 workers by default) + optional SPA mount for the frontend.
- **frontend-build** (one-shot): Builds the Vite/React app and leaves dist in a volume.
- postgres, redis, searxng: unchanged.
- celery_worker: unchanged (uses the same image).

## Networking for Ollama (Your LLM)

By default the containers reach your host Ollama via `host.docker.internal:11434`.

- macOS / Windows (Docker Desktop): works out of the box.
- Linux: may need `--add-host=host.docker.internal:host-gateway` (already in compose) or run `ollama serve` and point OLLAMA_BASE_URL at your LAN IP.
- Cloud LLM: don't touch `OLLAMA_BASE_URL` — just paste an API key (see next section).

## AI Backend (Ollama or any cloud API)

`app/llm/resolver.py` auto-detects the backend on every call (`LLM_PROVIDER=auto`, default):

1. **Ollama reachable** → uses it with your `CHAT_MODEL` / `VLM_MODEL` / `EMBEDDING_MODEL`.
2. **Otherwise** → the first cloud key set in `.env`, in order: `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → `GEMINI_API_KEY` → `XAI_API_KEY` → `DEEPSEEK_API_KEY`. Each provider has its own model setting (`OPENAI_CHAT_MODEL=gpt-4o`, `ANTHROPIC_CHAT_MODEL=claude-sonnet-4-6`, …) so Ollama tags are never sent to a cloud API.
3. **Neither** → every request answers 503 with exact instructions: *"No AI backend is configured. Put your API key or your Ollama connection in backend/.env…"*.

So "going cloud" is: paste one key into `.env`, `docker compose up -d api celery_worker` — done. Pin a backend explicitly with `LLM_PROVIDER=openai|anthropic|gemini|xai|deepseek|ollama|custom` if you don't want auto-detection.

Embeddings follow the same chain (only OpenAI/Gemini offer embedding APIs). When you switch the embedder permanently, pin `EMBEDDING_PROVIDER=openai` (or `gemini`) — stored vectors from the old model are wiped and the whole library re-embeds automatically at the next startup (summaries/figure descriptions are cached and don't re-run). All compose services pass these variables through from `backend/.env` already.

## Auto-Recovery (Self-Healing Containers)

Two layers, both already wired in `docker-compose.yml`:

1. **`restart: unless-stopped`** on every long-running service (postgres, redis, searxng, celery_worker, api, autoheal). A container that crashes or exits — e.g. the worker OOM-killed by a 700-page book (exit 137) — restarts automatically; queued uploads resume.
2. **`autoheal` watchdog** (`willfarrell/autoheal`, Docker socket mounted): restarts any container labeled `autoheal=true` (api, postgres, redis) whose healthcheck turns **unhealthy** — the "running but hung" case that restart policies can't see.

Neither mechanism touches data volumes. A deliberate `docker compose down` (or stopping the LAN script) is final — nothing restarts after that.

## Temporary LAN Server

`./start-lan-server.sh` brings up this whole stack with the `server` profile, removes the upload cap, raises the MinerU timeout for huge books, prints the LAN URL for other devices on the same network, and tears everything down on Ctrl+C (volumes preserved). Details: `docs/README.md` §6.6.

## Concurrency & Multiple Users

- DB connection pools are now configurable (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW` in .env — default 10/15).
- The `/ask` (and research) path is protected by a per-worker semaphore (`MAX_CONCURRENT_ASKS=3` default). Excess requests queue cleanly instead of smashing your GPU.
- uvicorn runs with `--workers 2` in the container (tune in compose if your server machine has more cores/RAM).

This setup is designed for "a few concurrent researchers on one powerful desktop/laptop/server box", not thousands of users.

## What Still Lives on the Host (by Design)

- Ollama (easy to swap later)
- Your papers and all generated artifacts (under `backend/app/storage` — bind-mounted)
- (Optionally) the real MinerU binary if you want highest quality extraction

## Rebuilding After Code Changes

```bash
docker compose build api
docker compose up -d api
```

The frontend-build step only needs re-running when you change UI code.

## Health & Logs

- Health: `curl http://localhost:8000/api/v1/health`
- Logs: `docker compose logs -f api`
- Full stack status: `docker compose ps`

## Troubleshooting First Build

- Image build takes 5-20 minutes the very first time (Python deps + node_modules).
- Subsequent starts are fast.
- If the SPA doesn't appear: check that `frontend-build` completed successfully and the volume has `index.html`.
- Port 8000 conflict: make sure no host uvicorn is running.

This is the productionized "my computer = server" experience you asked for. All non-LLM components are now first-class container citizens, the async path is hardened for concurrency, and the door is open for any LLM provider later.
