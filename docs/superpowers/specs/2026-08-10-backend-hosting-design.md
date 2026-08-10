# Design: Host the 9XAIPal backend 24/7 (Render) — sub‑project B

Date: 2026-08-10
Status: Draft for review

## Goal

Run the backend on a managed host with a public HTTPS URL so the **Vercel‑hosted
frontend can actually upload and read papers** — replacing today's "No backend
connected" state. Depends on sub‑project A (`EXTRACTOR_PROVIDER=vlm`, PR #8),
which removes MinerU/torch from the worker and makes a small box viable.

Non‑goals: custom domain, autoscaling, multi‑user auth, migrating the frontend
off Vercel, changing app behaviour.

## Platform decision: Render

Chosen over Fly.io because it is Docker‑native (reuses `backend/Dockerfile`
unchanged), gives a free HTTPS subdomain (`*.onrender.com`) with no domain
purchase, and offers managed Postgres **with pgvector** plus managed Redis
("Key Value") — the two stateful pieces this app needs.

## Architecture (and the constraint that shapes it)

**Render disks cannot be shared between services.** The Celery worker *writes*
extracted assets to `STORAGE_ROOT` and the API *serves* them, so a
"web service + separate background worker" split would break asset serving.

**Therefore: one Render Web Service runs BOTH processes**, sharing one disk —
mirroring the project's existing "my computer is the server" single‑box model.

```
Render Web Service  (Docker, backend/Dockerfile, Starter instance)
  ├── uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2
  └── celery -A app.core.celery_app worker --loglevel=info --concurrency 1
  └── Disk mounted at /data/storage   (STORAGE_ROOT)
Render Postgres  (pgvector)   ← DB
Render Key Value (Redis)      ← Celery broker/backend
Vercel (static SPA)  → VITE_API_BASE_URL = https://<service>.onrender.com
```

Both processes are started by a new `backend/start.sh` (worker in background,
uvicorn in foreground so the container's lifetime tracks the web process; the
script `exec`s uvicorn and traps signals so a crashed worker is visible in logs).

## Files to create/modify

- **Create `backend/start.sh`** — starts the Celery worker, then `exec`s uvicorn
  bound to `$PORT` (Render injects `PORT`).
- **Create `render.yaml`** (repo root) — Blueprint declaring the web service
  (Docker, `dockerfilePath: backend/Dockerfile`, `dockerContext: backend`),
  the disk (`/data/storage`, 10 GB), the Postgres instance and the Key Value
  instance, with env wiring (`fromDatabase` / `fromService`).
- **Modify `backend/Dockerfile`** — only if needed: ensure `start.sh` is copied
  and executable; keep the existing light `requirements.txt` install (no torch).
- **No application code changes.** Everything else is configuration.

## Configuration

| Variable | Value / source |
|---|---|
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | from the Render Postgres instance |
| `REDIS_URL` | from Render Key Value (`rediss://…`) |
| `STORAGE_ROOT` | `/data/storage` (the mounted disk) |
| `EXTRACTOR_PROVIDER` | `vlm` (requires PR #8 merged) |
| `OLLAMA_BASE_URL` | `https://ollama.com` |
| `OLLAMA_API_KEY` | **secret**, set in the Render dashboard (never committed) |
| `CHAT_MODEL` / `VLM_MODEL` / `EMBEDDING_MODEL` | the `-cloud` tags already in use |
| `EXTRACTOR_VLM_MODEL` | confirmed Qwen3‑VL cloud tag |
| `CORS_ORIGINS` | `https://9xaipal.vercel.app` (+ the `*-bbsc.vercel.app` preview host in use) |
| `VECTOR_DIMENSION` | must match the embedding model's output |
| `MAX_UPLOAD_SIZE_MB` | keep default 100 |

Frontend: set **`VITE_API_BASE_URL`** in Vercel → Settings → Environment
Variables to the Render URL, then redeploy (it is a build‑time value).

## Data flow

`Vercel SPA → https://<service>.onrender.com/api/v1/… → uvicorn → Postgres
(pgvector) / Redis → Celery worker (same container) → Qwen3‑VL & embeddings on
Ollama Cloud → assets written to /data/storage → served back by the API`

## Verification (acceptance)

1. `GET https://<service>.onrender.com/api/v1/health` returns healthy.
2. Render logs show migrations applied and `CREATE EXTENSION vector` succeeding.
3. From `https://9xaipal.vercel.app`: upload a small PDF → progress advances
   past *extracting → chunking → embedding* → the reader shows structural chunks
   (headings/figures/equations) — i.e. **no "No backend connected"**.
4. Ask a question in the side chat and get a grounded answer (LLM path works).
5. Re‑deploy the service and confirm previously uploaded papers still open
   (disk persistence).

## Cost (approximate, user‑paid)

Web Service Starter ≈ $7/mo + 10 GB disk ≈ $2.50/mo + Postgres Basic ≈ $7/mo +
Key Value (free tier may suffice) → **≈ $17–27/mo**. Ollama Cloud credits are
separate and consumed per extraction/chat.

## Risks / open questions

- **Free instance types spin down** and would suspend the Celery worker; the web
  service must be a paid always‑on instance for background processing to work.
- **Single‑container coupling:** a Celery crash doesn't restart the container.
  Mitigation: worker logs to stdout (visible in Render); revisit if it proves flaky.
- **Web search (SearXNG) is not deployed** — `EXTERNAL` questions will fail or
  degrade. Acceptable for v1; document it.
- **`VECTOR_DIMENSION` mismatch** with the chosen cloud embedding model would
  force a re‑embed; confirm the model's true dimension before first ingest.
- **Ollama Cloud rate limits** — sub‑project A's deferred fast‑follows
  (per‑page retry/backoff, dead `extractor_vlm_concurrency`) should land before
  large books are processed.
- **Upload size vs. Render request limits** — verify large PDFs upload cleanly.

## Prerequisites (user actions I cannot perform)

1. Merge PR #8 (`EXTRACTOR_PROVIDER=vlm`).
2. Create the Render account and the services (I cannot create accounts or enter
   payment details); paste the generated URL back to me.
3. Set `OLLAMA_API_KEY` as a secret in the Render dashboard.
