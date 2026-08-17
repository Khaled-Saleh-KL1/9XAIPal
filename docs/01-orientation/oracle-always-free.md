# Deploying 9XAIPal "entirely on the web" — Oracle Cloud Always Free + Vercel

**Goal:** run the whole app without your local machine. The backend lives on a
free, always-on Oracle ARM VM; the UI lives on Vercel; chat and vision run on
Ollama Cloud; embeddings run on Gemini.

## Why not "everything on Vercel"?

Vercel is a static + serverless platform. It **cannot** run this backend, which
needs a persistent Celery worker, Postgres+pgvector, Redis, and file storage.
So the split is:

```
Vercel (static SPA, 9xaipal.vercel.app)
   └─ VITE_API_BASE_URL ──▶ https://<ip-with-dashes>.sslip.io
                              │  (Caddy, ports 80/443, Let's Encrypt)
                              ▼
                            Oracle VM (Always Free, always on)
                              ├─ caddy          ← the ONLY public entrypoint
                              ├─ api            (FastAPI/uvicorn, 127.0.0.1:8000)
                              ├─ celery_worker  (ingestion)
                              ├─ postgres       (pgvector)   ┐
                              ├─ redis                       ├ internal only
                              └─ searxng                     ┘
   chat + vision ──▶ Ollama Cloud (OLLAMA_API_KEY)
   embeddings    ──▶ Gemini      (GEMINI_API_KEY)
```

Postgres, Redis and SearXNG are **not** published to the host — they are
reachable only by service name on the compose network. The API binds to
`127.0.0.1:8000` for local health checks. Everything public goes through Caddy.

## Models — verified 2026-08-17

| Purpose | Model | Provider | Env var |
|---|---|---|---|
| Chat / Q&A | `gemma4:31b` | Ollama Cloud | `CHAT_MODEL` |
| Vision / PDF extraction | `gemma4:31b` | Ollama Cloud | `VLM_MODEL`, `EXTRACTOR_VLM_MODEL` |
| Embeddings | `gemini-embedding-001` | Gemini | `EMBEDDING_MODEL` |

Three findings that shaped this table — all confirmed against the live APIs:

1. **`qwen3-vl:235b` was retired by Ollama on 2026-06-16.** It now returns
   `{"error":"qwen3-vl:235b was retired..."}`. `gemma4:31b` is multimodal and
   was verified to transcribe a real paper page correctly at 180 DPI, so it
   serves as both chat and vision model.
2. **This Ollama key has no embedding access** — every embedding model returns
   `{"error":"unauthorized"}`. Embeddings therefore run on Gemini.
   `gemini-embedding-001` returns exactly **1024** dimensions, matching
   `VECTOR_DIMENSION`, so no truncation or zero-padding occurs. Retrieval uses
   cosine distance (`<=>`, `vector_cosine_ops`), so the vectors not being
   L2-normalised is harmless.
3. **`EMBEDDING_PROVIDER` must be pinned to `gemini`.** With the default
   `auto`, the resolver probes Ollama first, finds `https://ollama.com`
   reachable, selects it, and then fails on every embed call.

`EXTRACTOR_PROVIDER=vlm` routes PDF extraction to the cloud VLM, so **no MinerU
and no 5 GB model download**. The images build from `Dockerfile.oracle` +
`requirements.oracle.txt` (`requirements.txt` minus `mineru[core]`) — nothing in
`app/` imports MinerU, it is only ever shelled out to, so dropping it also drops
torch/OpenCV/transformers and their patchy aarch64 wheels.

## Step 1 — Create the Oracle VM

1. Console → **Compute → Instances → Create instance**.
2. Shape **`VM.Standard.A1.Flex`** (ARM, Always Free) — 4 OCPUs, 24 GB RAM,
   200 GB boot volume. Image: **Ubuntu 24.04**. Add your SSH public key.
3. Ensure the subnet's security list allows ingress **22, 80, 443** from
   `0.0.0.0/0`, and that the VCN has an **Internet Gateway** with a
   `0.0.0.0/0` route. Without the gateway the VM has no internet at all.

> **"Out of host capacity"** is extremely common for the free ARM shape and is
> returned as an HTTP **500**, which the OCI SDK silently retries with backoff —
> a single `oci compute instance launch` can appear to hang for many minutes.
> Pass `--no-retry` to fail fast and loop over all three availability domains
> yourself. Capacity can stay exhausted for hours or days; upgrading to
> Pay As You Go usually clears it, and the 4 OCPU/24 GB A1 stays free.

## Step 2 — Put the code on the VM

```bash
ssh -i ~/.ssh/9xaipal ubuntu@<VM_PUBLIC_IP>
sudo apt update && sudo apt install -y git
git clone https://github.com/Khaled-Saleh-KL1/9XAIPal.git
cd 9XAIPal/backend
```

## Step 3 — Configure

```bash
cp .env.oracle .env
```

Set these four in `.env`:

| Var | Value |
|---|---|
| `OLLAMA_API_KEY` | from https://ollama.com/settings/keys |
| `GEMINI_API_KEY` | from https://aistudio.google.com/apikey |
| `POSTGRES_PASSWORD` | a long random string |
| `CADDY_SITE_ADDRESS` | the VM's public IP with dots→dashes + `.sslip.io`, e.g. `152-67-12-34.sslip.io` |

`sslip.io` resolves `a-b-c-d.sslip.io` to `a.b.c.d` with zero DNS setup, which
gives Caddy a real hostname to obtain a Let's Encrypt certificate for. HTTPS is
**not optional**: the Vercel frontend is served over HTTPS, and a browser
refuses to call a plain `http://` backend from it (mixed content).

## Step 4 — Start the stack

```bash
./oracle-setup.sh      # installs Docker, opens 80/443, builds, starts, health-checks
docker compose -f docker-compose.oracle.yml logs -f api
```

Oracle's Ubuntu images ship an iptables chain that rejects everything except
port 22; `oracle-setup.sh` opens 80/443 and persists the rules. If port 80 is
filtered, Caddy's ACME challenge fails and no certificate is issued.

Confirm: `curl https://<CADDY_SITE_ADDRESS>/api/v1/health` →
`{"status":"ok","database":"ok",...}`

## Step 5 — Point Vercel at the backend

In Vercel → project **9xaipal** → **Settings → Environment Variables**:

```
VITE_API_BASE_URL = https://<CADDY_SITE_ADDRESS>
```

Then **redeploy** — Vite bakes this in at build time, so an existing deployment
will not pick it up.

## Cost

- Oracle `VM.Standard.A1.Flex` (4 OCPU / 24 GB): **$0** (Always Free)
- Vercel Hobby: **$0**
- Ollama Cloud: credits per chat/vision call
- Gemini embeddings: free tier (~100 req/min; a large upload may need retries)

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Out of host capacity` | No free ARM hardware. Retry across all 3 ADs; consider Pay As You Go. |
| Frontend shows "No backend connected" | `VITE_API_BASE_URL` unset, or set but not redeployed. |
| Browser blocks requests | Backend on `http://`. It must be HTTPS. |
| No certificate issued | Port 80 blocked (security list or VM iptables). |
| Uploads ingest but search returns nothing | Embeddings failing — check `EMBEDDING_PROVIDER=gemini` is pinned. |
| Extraction quality poor, logs show "PyMuPDF text fallback" | The VLM reply failed to parse; see `vlm_client._strip_code_fence`. |
| `*.vercel.app` unreachable on your network | Some ISPs/campus networks block it by TLS SNI. Test on mobile data. |

## Files

- `backend/docker-compose.oracle.yml` — the stack (Caddy + api + worker + pg + redis + searxng)
- `backend/Dockerfile.oracle` / `requirements.oracle.txt` — lean ARM image, no MinerU/torch
- `backend/Caddyfile` — HTTPS reverse proxy
- `backend/.env.oracle` — env template
- `backend/oracle-setup.sh` — one-command VM bootstrap
