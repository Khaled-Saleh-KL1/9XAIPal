# Setup & running

> **What this is:** the path from a fresh clone to a working app, plus the checks that prove it
> worked. Follow it top to bottom the first time.
>
> **How to read it:** §1 prerequisites → §2 configure → §3 start → §4 verify → §5 deployment
> modes → §6 first-run traps.
>
> **Owns:** install and bring-up procedure.
> **Does not own:** what each env var means ([configuration.md](../03-reference/configuration.md)),
> what listens where ([runtime-topology.md](runtime-topology.md)), fixing a broken install
> ([operations.md](operations.md)).
>
> **Status:** current · **Last verified:** 2026-07-28 (`main`, 5471870): §5 run end-to-end from
> `docker compose down` to a healthy stack
> **Verify with:** `curl -s localhost:8000/api/v1/health | jq`

---

## 1. Prerequisites

| Tool | Version | Why |
| --- | --- | --- |
| Python | 3.11+ | Backend |
| Node.js | 18+ | Frontend (Vite 6, React 19) |
| Docker + Compose | latest | Postgres / Redis / worker |
| PostgreSQL | 15+ (16 in compose) | Needs `pgvector` + `uuid-ossp` |
| Redis | 7+ | Celery broker |
| MinerU | 3.2+ | `mineru` CLI. ⚠ `magic-pdf` 0.x is a different, abandoned package and is **not** supported |
| Ollama | latest | Optional, a cloud API key works instead |

### MinerU: installed by uv, but the weights are not

MinerU is pinned in `pyproject.toml`'s `mineru` extra (`mineru[core]>=3.4.4`) as of 2026-07-25, so
`uv sync --extra mineru` brings in the CLI. What it does **not** bring is the model
weights: the first parse downloads ~5 GB from Hugging Face. Set `HF_TOKEN` if you get
rate-limited.

```bash
mineru --version       # must succeed in the shell the Celery worker inherits
```

Verified working on Apple Silicon (M4 Max, macOS 15.7) directly on the host, no CUDA and no
container needed. ⚠ Do not change the backend: the app passes `-b pipeline`, and
`hybrid-engine` measured 8.5× slower with worse heading classification
([evaluation](../plans/pdf-parser-evaluation.md)).

⚠ If you skip MinerU and set `ALLOW_PYMUPDF_FALLBACK=true`, extraction still runs but produces
**no OCR, no table structure, and no math**, which removes most of the reason this app exists.
Fine for a smoke test, wrong for real use. Running the worker via
`docker compose` sidesteps this entirely: `Dockerfile.mineru` bakes MinerU and its models into the
image.

### One AI backend is required

Either Ollama with models pulled, **or** one cloud API key. Neither ⇒ chat returns 503 with
setup instructions (papers still upload and read). Full chain:
[ai-backend.md](../02-architecture/ai-backend.md).

```bash
ollama pull gemma4:31b-cloud       # or whatever you set as CHAT_MODEL
ollama pull qwen3-embedding:8b     # EMBEDDING_MODEL — note the explicit tag
```

⚠ Use a tag that exists. `EMBEDDING_MODEL` defaults to the bare name `qwen3-embedding`, which
404s at first embed if only tagged variants (`:4b`, `:8b`) are pulled.

---

## 2. Configure

```bash
cd backend
cp .env.example .env
```

Then edit `.env`. The minimum that must be right: Postgres credentials, one AI backend, and an
`EMBEDDING_MODEL` tag that exists. Everything else has a working default:
[configuration.md](../03-reference/configuration.md) is the full table.

---

## 3. Start

### 3.1 Infrastructure

```bash
cd backend
docker compose up -d postgres redis
```

### 3.2 Backend (host)

```bash
cd backend
uv sync --extra mineru  # creates .venv and installs everything, pinned exactly by uv.lock
source .venv/bin/activate

uvicorn app.main:app --reload --port 8000
```

⚠ Skip the `mineru` extra (plain `uv sync`) if you don't need PDF extraction on the host — it
skips MinerU and torch, both multi-hundred-MB. `Dockerfile.lite` (the `api` service) does the same.

### 3.3 Celery worker (separate terminal, same venv)

```bash
cd backend && source .venv/bin/activate
celery -A app.core.celery_app worker --loglevel=info
```

⚠ Without this, uploads accept and then hang at `queued` forever. Nothing surfaces an error:
the job simply sits in Redis with no consumer. If the processing overlay never advances, check
here first.

### 3.4 Frontend

```bash
cd frontend
npm install
npm run dev              # :5173, proxies /api and /static to :8000
```

### 3.5 What happens on startup

The FastAPI lifespan ([`core/lifecycle.py`](../../backend/app/core/lifecycle.py)) runs, in order:

1. Configure logging.
2. Warn if Postgres still uses the default development password.
3. Create every storage directory (`documents/`, `extracted/`, `images/`, `assets/`, `logs/`).
4. Mount the built SPA at `/` when `SERVE_FRONTEND=true` and a `dist` exists.
5. Verify the database connection.
6. Apply migrations (`database/schema.sql`, idempotent).
7. Resolve and log the active AI backend, never fatal.
8. Sync the pgvector column to `VECTOR_DIMENSION` and ensure the HNSW index.
   ⚠ **If the dimension changed, all embeddings are wiped and re-queued.**
9. If it did not change, check for an embedding-model switch: pinned provider ⇒ wipe + re-embed;
   `auto` ⇒ warn only.

---

## 4. Verify

```bash
# 1. health
curl -s http://localhost:8000/api/v1/health | jq
# expect: {"status":"ok","database":"ok","ollama":"ok","web_search":"ok","web_search_provider":"tavily"}

# 2. upload
curl -s -F "file=@../samples/attention-is-all-you-need.pdf" \
  http://localhost:8000/api/v1/papers/upload | jq
# expect: 201 {"id":"<uuid>","status":"processing", ...}

# 3. poll until complete
curl -s http://localhost:8000/api/v1/papers/<uuid>/progress | jq
# queued → extracting → chunking → embedding → summarizing → complete

# 4. read the first chunk
curl -s http://localhost:8000/api/v1/papers/<uuid>/chunks/1 | jq

# 5. ask
curl -s -X POST http://localhost:8000/api/v1/papers/<uuid>/ask \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the main contribution of this paper?"}' | jq
```

Then in the browser: open `http://localhost:5173`, drag a PDF onto the library, wait for the
overlay to finish, click the card, ask a question in the right pane.

A sample paper ships at [`samples/attention-is-all-you-need.pdf`](../../samples/) for exactly this.

---

## 5. Deployment modes

| Mode | Command | Use when |
| --- | --- | --- |
| Host dev | §3 above | Normal development, hot reload on both sides |
| Full stack in Docker | `cd backend && docker compose up -d --build` | UI + API on `:8000`, no Node needed on the host |
| LAN server | `cd backend && ./start-lan-server.sh` | Let another device on the same Wi-Fi use the app |

**Full stack** brings up every service: Postgres, Redis, the Celery worker, the API, and a one-shot
container that builds the SPA into a volume the API serves at `/`. The whole app is on one port;
there is no second dev server and no reverse proxy.

`[historical]` This used to require `--profile server`. Without the flag the frontend build never
ran, so `up` produced an API with an empty SPA volume that served nothing at `/`. The build service
is no longer behind a profile, and `api` waits for it via
`depends_on: {frontend-build: {condition: service_completed_successfully}}`, which also makes a
broken frontend build **fail the `up` loudly** instead of quietly starting an API with no UI.

⚠ **If another project on your machine already holds `8000`, `5432` or `6379`, `up` fails
with "port is already allocated."** Every mapping is overridable in `backend/.env`:
`API_PORT`, `POSTGRES_PORT`, `REDIS_PORT` (see
[`.env.example`](../../backend/.env.example)). Only the **host** side moves: containers reach each
other by service name on the compose network, so nothing inside the stack is affected.

`start-lan-server.sh` builds the SPA in a container, removes the upload cap, raises the MinerU
timeout for large books, prints the exact LAN URL, streams logs, and tears the stack down on
Ctrl+C: **without** `-v`, so data volumes and uploaded papers survive.

The API requires a logged-in session (see [auth.md](../02-architecture/auth.md)) the same way in
LAN mode as anywhere else: nothing disables `get_current_user` for it. The real gate is
`SIGNUP_INVITE_CODE`: leave it set to something only people you trust have, since anyone on the
LAN who obtains it can create their own account. Leaving it empty closes signup entirely (existing
accounts can still log in), the safer default for a LAN you don't fully control.
⚠ `/static/{images,extracted,assets}` are the exception: those mounts have no auth check at all.
See [roadmap.md](../roadmap.md).

---

## 6. First-run traps

Collected because each one has cost someone an hour:

| Symptom | Cause | Fix |
| --- | --- | --- |
| Upload sticks at `queued` forever | No Celery worker consuming Redis | Start the worker (§3.3) |
| `docker compose up` fails with "port is already allocated" | Another project holds `8000` / `5432` / `6379` | Set `API_PORT` / `POSTGRES_PORT` / `REDIS_PORT` in `backend/.env`, host side only |
| Worker logs `Cannot connect to redis://redis:6379: Name or service not known` | A container left over from an older `up` is attached to no compose network | `docker compose up -d --force-recreate redis celery_worker` |
| First embed 404s | `EMBEDDING_MODEL` has no matching pulled tag | Use an explicit tag, e.g. `qwen3-embedding:8b` |
| Every model call refused, in Docker | `OLLAMA_BASE_URL=localhost` inside a container resolves to the container | Compose already sets `host.docker.internal`; do not override it from the host `.env` |
| Web search silently returns nothing | No provider configured, or every configured one is down/out of quota | Set at least one of `TAVILY_API_KEY` (accepts a comma-separated list) / `LINKUP_API_KEY` / `EXA_API_KEY` / `SERPAPI_API_KEY`; check the logs for `<provider> search failed: ...`. ⚠ `/health` cannot verify a key works, only that one is configured: see [configuration.md § Web search](../03-reference/configuration.md#web-search) |
| Ingestion fails with `MinerUError` | MinerU missing from the worker's `$PATH` | Install it, or run the worker in compose |
| Chat 503 `NO_LLM_CONFIGURED` | No Ollama and no cloud key | Start Ollama or paste one API key |
| Setting seems to do nothing | Typo'd env key: `extra="ignore"` swallows unknown keys silently | Check spelling against [configuration.md](../03-reference/configuration.md) |
