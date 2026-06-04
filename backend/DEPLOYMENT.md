# Deployment: "My Computer Is the Server"

This document describes how to run the full 9XAIPal stack (API + UI + workers + infra) in containers so that **your machine acts as the server**.

Ollama (the LLM/VLM/embeddings) can stay on the host (fastest for now) or be replaced later by any cloud endpoint (Grok, Gemini, GPT, Claude, self-hosted vLLM, etc.). The rest of the stack is now fully containerized and async-ready for multiple concurrent users on the same machine.

## Quick Start (Recommended for Your Machine as Server)

```bash
cd backend

# 1. Copy and review env (no secrets in the example)
cp .env.example .env
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
- Later (cloud LLM): just change `OLLAMA_BASE_URL` and `CHAT_MODEL` / `VLM_MODEL` in .env. No other code changes needed for the rest of the system.

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

## Future LLM Swap (Grok / Gemini / GPT / Claude)

When you are ready:

1. Point `OLLAMA_BASE_URL` at an OpenAI-compatible endpoint (or add a thin adapter in `app/llm/`).
2. Update model names.
3. Everything else (context routing, research agent, sub-threads, compaction, citations, image persistence, ingestion pipeline, etc.) continues to work unchanged.

The architecture was deliberately built with this future in mind.

## Troubleshooting First Build

- Image build takes 5-20 minutes the very first time (Python deps + node_modules).
- Subsequent starts are fast.
- If the SPA doesn't appear: check that `frontend-build` completed successfully and the volume has `index.html`.
- Port 8000 conflict: make sure no host uvicorn is running.

This is the productionized "my computer = server" experience you asked for. All non-LLM components are now first-class container citizens, the async path is hardened for concurrency, and the door is open for any LLM provider later.
