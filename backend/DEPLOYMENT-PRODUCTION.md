# Deployment: public VPS with a real domain, nginx, and CI/CD

This document describes a **different** scenario than [`DEPLOYMENT.md`](DEPLOYMENT.md) in this
same directory: that one is "your own machine is the server" (local/LAN, no domain, no TLS, plain
`http://localhost:8000`). This one is the real, live setup: a small VPS, a public domain,
real HTTPS, and a push-to-deploy pipeline. It documents the actual production deployment as it
exists today, so the next domain migration, cert renewal question, or "why is this container
stuck" doesn't have to be re-derived from scratch.

**No secrets live in this file, in code, or in CI/CD. Ever.** Every credential
(`OLLAMA_API_KEY`, `POSTGRES_PASSWORD`, `HF_TOKEN`, `TAVILY_API_KEY`, …)
exists in exactly one place: the untracked `backend/.env` file on the server, `chmod 600`. Every
reference below is to the variable *name*, never a value. See
[configuration.md](../docs/03-reference/configuration.md) for the full list of names.

**This deployment, concretely:** `https://9xaipal.kl1.site`, a DuckDNS free subdomain until
2026-08-27, when it moved to a purchased domain (see §7 for exactly how that migration went). A
domain and a server's public IP aren't secrets (the same category of information as a phone book
entry), so they're stated plainly here and in §6/§7 rather than genericized into a placeholder.

---

## 1. Architecture

```text
Browser
   │  HTTPS (Let's Encrypt cert, certbot auto-renewal)
   ▼
nginx (host, :80/:443)
   │  server_name <your-subdomain>
   │  same-origin: SPA + /api on one origin, no CORS preflight, no mixed content
   │
   ├── /api/*, /static/*, /openapi.json, /docs*, /redoc*  ──► 127.0.0.1:8000 (api container)
   └── everything else                                     ──► static files, backend/frontend-dist/

api container (FastAPI, SERVE_FRONTEND=false, bound to 127.0.0.1 only, never exposed directly)
   │
   ├── postgres (pgvector)  ─┐
   ├── redis                 ├─ internal Docker network only, not published to the host
   └── celery_worker ────────┘
          │
          ├─► Ollama Cloud (chat + vision): OLLAMA_BASE_URL=https://ollama.com
          └─► this host's own local Ollama (embeddings): host.docker.internal:11434

autoheal container: watches every labeled container's healthcheck, restarts on "unhealthy"
Docker daemon (enabled at boot) + restart:unless-stopped on every service
  → survives a VPS reboot, a container crash, AND a container hang (see §4)

Self-hosted GitHub Actions runner: lives on this same box, deploys on every push to main
```

Same-origin is a deliberate simplification, not an accident: `CORS_ORIGINS` stays empty in
production because nginx serves the built SPA *and* proxies `/api` from the same public origin, so
there is no separate frontend host to allow.

---

## 2. Compose file

One file: `docker-compose.prod.yml` is this deployment directly, not a generic base plus an
overlay for this specific box:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Worth knowing about what it does, since none of it is obvious from the file alone:

- `celery_worker` builds from `Dockerfile.mineru` (adds MinerU + torch + the OpenCV/runtime libs
  it needs), and this VPS (6 CPU / 11GB RAM / x86_64, no GPU) has the CPU/RAM for real local
  extraction, so there's no need for a cloud-VLM fallback. `api` builds from the lighter
  `Dockerfile.lite` instead: it never runs MinerU itself (`celery_worker` does all extraction),
  so its image has no reason to carry torch/OpenCV too.
- `HF_TOKEN` reaches `Dockerfile.mineru`'s `mineru-models-download` step (which runs at *build*
  time, avoiding anonymous Hugging Face rate limits on the ~5GB weight download) via a BuildKit
  build-time **secret**, not a build arg or an environment variable: either of those ends up
  permanently readable in `docker history --no-trunc` and in `docker inspect` of any container
  run from the image; a secret exists only inside that one `RUN` step and is never written to a
  layer. `docker-compose.prod.yml`'s `secrets:` block sources it from `HF_TOKEN` in this file.
- `MINERU_PAGE_BATCH_SIZE`: extracts large documents in page-range batches. This does double
  duty: it bounds peak RAM (a huge book extracted in one pass can OOM-kill the worker), *and*
  it's the granularity of the real extraction-progress reporting the UI shows (see
  [`mineru_client.py`](app/extraction/mineru_client.py)'s `on_progress` callback,
  [`pipeline_sync.py`](app/extraction/pipeline_sync.py)'s `update_job_progress_sync`), a
  smaller value means more visible progress movement during a long extraction, not just OOM
  safety. `0` disables batching entirely.
- `WORKER_MEM_LIMIT` (default `6G`) caps `celery_worker`'s memory so a huge PDF OOMs only that
  one container (which then auto-restarts, see §4) instead of pressuring postgres/redis/api on
  the same box. There is no swap on this box, so a real overrun hits this hard rather than
  degrading gracefully; that's deliberate, the same isolation tradeoff as the paragraph above.
  Kept at 6G rather than higher: this box also hosts a portfolio site and another small app
  alongside 9XAIPal, so the remaining ~5GB matters more than it would on a single-purpose box.

---

## 3. AI backend split

Chat and vision run on **Ollama Cloud** (`OLLAMA_BASE_URL=https://ollama.com`, `OLLAMA_API_KEY`
set), since a model large enough to be worth using can't run at usable speed on this CPU-only box.
Embeddings run on **this host's own local Ollama** instead
(`EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1`, model `qwen3-embedding:0.6b`), small,
free, and fast enough locally that there's no reason to pay for it.

⚠ **This split only works because of two host-level fixes that live outside this repo entirely,
easy to forget when replicating this setup, and exactly the kind of thing that silently breaks
everything downstream of it (`chunk_embeddings` staying empty with no obvious error) if missed:**

1. **Ollama must bind to all interfaces, not just localhost.** By default `ollama serve` listens
   on `127.0.0.1:11434` only, reachable from the host itself, but **never** from a Docker
   container, since a container reaches the host through the Docker bridge gateway IP
   (`docker network inspect <network> --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'`), not
   `127.0.0.1`. Fix via a systemd override (not in this repo):
   ```
   sudo mkdir -p /etc/systemd/system/ollama.service.d
   sudo tee /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
   [Service]
   Environment="OLLAMA_HOST=0.0.0.0:11434"
   EOF
   sudo systemctl daemon-reload && sudo systemctl restart ollama
   ```
2. **The firewall must allow it, scoped to the Docker bridge only.** UFW's default policy is
   deny-incoming except the ports explicitly opened (SSH, 80, 443), and port 11434 was never one of
   them, since it was never reachable from outside the host before fix #1. Opening it to the
   *whole internet* would be wrong (Ollama has no auth on this endpoint); scope it to the Docker
   bridge subnet specifically:
   ```
   sudo ufw allow from <docker-bridge-subnet> to any port 11434 proto tcp
   ```
   Find the subnet with `docker network inspect <network> --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`.

Verify end-to-end from inside the container that actually needs it, not just from the host shell
(a host-shell `curl localhost:11434` succeeds via loopback regardless of either fix above, and
proves nothing about container reachability):

```bash
docker exec <celery_worker container> python3 -c \
  "import httpx; print(httpx.get('http://host.docker.internal:11434/api/version', timeout=5).text)"
```

### Embedding concurrency

`EMBEDDING_MAX_CONCURRENCY` (default `2`) controls how many embedding batch requests run at once
against local Ollama, measured directly on this hardware, not guessed:

| Concurrency | Measured time for the same workload |
| --- | --- |
| 1 (sequential) | baseline |
| **2 (current default)** | **best measured**: a single batch request only occupies ~2.8 of 6 cores, so a second one overlaps into genuinely idle capacity |
| 3 | **~4x slower than 2**, not faster: real contention, not more parallelism |

Don't casually raise this without re-measuring on the actual target hardware; the "more
concurrency must be faster" intuition measurably does not hold past the sweet spot here.

---

## 4. Self-healing (crash *and* hang)

Two independent mechanisms, both required: they cover different failure modes:

1. **`restart: unless-stopped`** on every long-running service. Covers a container that **exits**
   (crash, OOM-kill, uncaught panic). Does **not** cover a container that's still running but
   stuck: Docker has no way to know a hung process from a healthy one just from "is the process
   alive".
2. **`autoheal`** (`willfarrell/autoheal`, Docker socket mounted) watches every container labeled
   `autoheal=true` and restarts it if its healthcheck reports **unhealthy**: this is what catches
   "running but hung". `celery_worker`'s own healthcheck is `celery inspect ping`, which
   round-trips through the broker (Redis) rather than just checking the process is alive, and it
   answers even under a busy `--concurrency=1` extraction as long as the worker's control-plane
   thread is genuinely responsive, and only that is what autoheal restarts on.

⚠ A `docker stop`/`docker kill` from the CLI is treated as *deliberate* and does **not** trigger
`restart: unless-stopped`, that's correct Docker behavior, not a bug, and it means testing this
guarantee requires actually crashing something (e.g. `kill -9` the container's real PID on the
*host*, found via `docker inspect --format '{{.State.Pid}}' <container>`: a `docker exec kill`
from *inside* the container's own PID namespace gets silently suppressed by the kernel, since a
namespace's init process ignores signals sent to itself from within).

**Outside Docker entirely**, two more processes need the same guarantee and didn't have it by
default: nginx and the actions-runner both ship with no `Restart=` policy (nginx's is Debian's
package default; the runner's own generated unit has none either), so either one dying used to
mean no automatic recovery regardless of how healthy every container was. Fixed with a systemd
drop-in on each:

```bash
sudo mkdir -p /etc/systemd/system/nginx.service.d
sudo tee /etc/systemd/system/nginx.service.d/override.conf <<'UNIT'
[Service]
Restart=on-failure
RestartSec=5
UNIT

sudo mkdir -p /etc/systemd/system/actions.runner.Khaled-Saleh-KL1-9XAIPal.ovh-server.service.d
sudo tee /etc/systemd/system/actions.runner.Khaled-Saleh-KL1-9XAIPal.ovh-server.service.d/override.conf <<'UNIT'
[Service]
Restart=always
RestartSec=5
UNIT

sudo systemctl daemon-reload
sudo systemctl restart nginx actions.runner.Khaled-Saleh-KL1-9XAIPal.ovh-server.service
```

---

## 5. CI/CD: self-hosted runner, no secrets over the wire, gated on CI, self-healing on failure

`.github/workflows/deploy.yml` runs on a **self-hosted** GitHub Actions runner living on this same
box. Deliberately self-hosted rather than GitHub-hosted: **no secret ever needs to leave the box**:
`backend/.env` lives permanently on the server and is never read, echoed, or referenced by the
workflow. `permissions: contents: read` is the workflow's entire GitHub-side permission footprint
(plus `deployments: write`, purely cosmetic, it's what lets the job show up as a tracked GitHub
Deployment with a status and a URL on the repo homepage).

**Trigger: gated on CI actually passing, not the raw push.** `deploy.yml` and `.github/workflows/
ci.yml` used to be two independent workflows both triggered by `push: main` with no ordering
between them, so a push that failed CI would still deploy, and since this repo's branch protection
has `enforce_admins: false`, a direct push bypassing a PR entirely would deploy with zero
verification. `deploy.yml` now triggers on `workflow_run` for CI's completion, gated on
`conclusion == 'success'`: a deploy can only happen once CI's own run for that exact commit has
actually passed, regardless of how the commit reached `main`. Every commit reference in the
workflow uses `github.event.workflow_run.head_sha`, not `github.sha`: for a `workflow_run`-
triggered job, `github.sha` resolves to the default branch's current tip, which is only guaranteed
to match the validated commit if nothing else pushed in the gap between CI finishing and this job
starting.

⚠ **`actions/checkout` unconditionally deletes and recreates its target directory on every run**:
`clean: false` only skips an *additional* git-clean on top of that, it does not prevent the
delete-and-recreate. This bit twice during initial setup: checking out directly into the live
deployment directory destroyed `backend/.env` (secrets, gone) and the bind-mounted
`backend/app` source the running `celery_worker` depended on, breaking it mid-flight. The fix,
already in `deploy.yml`: checkout runs in the runner's own disposable workspace (`fetch-depth: 0`,
full history, not just this commit, needed for the rollback below), and only a one-way
`rsync -a --delete` (explicitly excluding `.git/`, `backend/.env`, `backend/app/storage/`,
`backend/frontend-dist/`, `.last-good-sha`) syncs tracked files into the real `DEPLOY_DIR`:
checkout and the live directory are never the same path.

**Automatic rollback on a failed deploy.** The actual build+restart+health-check sequence lives in
`scripts/deploy-once.sh`, not inline in the workflow: one script, so the rollback path below can
call the exact same sequence instead of a second, hand-maintained copy of it drifting out of sync.
After a deploy passes its health check, its commit is recorded as `$DEPLOY_DIR/.last-good-sha`. If
a *future* deploy builds cleanly (it passed CI, after all) but breaks at runtime (`docker compose
up -d --build` had already replaced the old, working containers by the time the health check
fails), the workflow restores `$DEPLOY_DIR` to `.last-good-sha` (via `git worktree add` against
the runner's full history) and re-runs `deploy-once.sh` against it. The workflow still reports
failure either way: a step already failed, and nothing in the rollback changes that. This only
decides whether the *site* stays down while the bad commit gets a fix.

The deploy job also builds the frontend in a throwaway `node:20-alpine` container with
`--user "$(id -u):$(id -g)"`: without it, files written by the containerized build come out
root-owned on the host, which then blocks the *next* run's `actions/checkout` cleanup (and any
manual `rm -rf`) with a permission error.

`VITE_API_BASE_URL` (baked into the frontend bundle at build time) and the job's
`environment.url` (the tracked GitHub Deployment link) both hardcode the current public domain:
see §7 for what to update when that changes.

---

## 6. DNS and TLS

One A record, on a **subdomain**, not the bare domain (e.g. `app.example.com`, not
`example.com`), pointing at the server's public IP. Verify propagation before touching nginx:

```bash
dig +short @8.8.8.8 <subdomain> A
dig +short @1.1.1.1 <subdomain> A
```

certbot (`--nginx` plugin) obtains the cert and installs a systemd timer that renews it
automatically; nothing manual is needed after the initial issuance.

---

## 7. Migrating to a new domain

This deployment has done exactly this once already (moved off a free DuckDNS subdomain onto a
real purchased domain). The order matters: doing it out of order breaks nginx in a way that's
easy to cause and mildly annoying to unwind:

1. Add the DNS A record (§6), confirm it resolves.
2. **Get the new cert before touching `server_name`**, using `certonly` mode: it only obtains
   cert files, it does not edit nginx config at all, which sidesteps a chicken-and-egg problem:
   ```
   sudo certbot certonly --nginx -d <new-domain> --non-interactive --agree-tos -m <your-email>
   ```
   (`certbot --nginx -d <new-domain>`, *without* `certonly`, only works cleanly if `server_name`
   already matches, otherwise its authenticator has no matching server block to attach the
   ACME challenge to.)
3. Update nginx: `server_name` to the new domain, and the two `ssl_certificate*` lines to the new
   cert's path (`/etc/letsencrypt/live/<new-domain>/{fullchain,privkey}.pem`).
   ⚠ **Don't blindly `sed` the old domain to the new one across the whole file in one pass**: the
   `ssl_certificate` lines contain the old domain too (it's part of the cert *path*), so a global
   find-replace repoints them at a cert file that doesn't exist yet, and `nginx -t` fails until
   it's manually reverted. Change `server_name` first, confirm `nginx -t` still passes (it will,
   nginx doesn't validate that a cert's path matches `server_name`), get the cert via step 2, *then*
   repoint the cert paths as a second, separate change.
4. `nginx -t && systemctl reload nginx`.
5. Update the two repo references to the old domain (`.github/workflows/deploy.yml`'s
   `environment.url` and its `VITE_API_BASE_URL` build arg) through a normal PR, same as any
   other code change.
6. Merge → the deploy pipeline rebuilds the frontend against the new domain and redeploys
   automatically.
7. Verify end-to-end: `curl https://<new-domain>/api/v1/health`, check the served bundle actually
   references the new domain (`curl -s https://<new-domain>/ | grep -o '/assets/index-[^"]*\.js'`,
   then grep that bundle for the domain string), confirm the old domain now fails TLS verification
   (expected: the cert no longer covers it).
8. If retiring the old domain's DNS provider entirely: remove its update cron job/script, and stop
   pointing its A record here (or delete it) so nothing keeps quietly trying to renew a cert nobody
   uses anymore.

---

## 8. Health & logs

- Health (from the host): `curl -sf http://127.0.0.1:8000/api/v1/health`, never exposed directly
  to the internet, only reachable on the box itself; the public path is always through nginx.
- Health (public): `curl -sf https://<domain>/api/v1/health`
- Logs: `docker compose -f docker-compose.prod.yml logs -f <service>`: every container is capped
  at 50MB (10MB × 5 files, `json-file` driver) via the compose file's `x-logging` anchor, so an
  unattended box's disk doesn't slowly fill from log growth alone. No `/etc/docker/daemon.json`
  default exists on this box, so a service added without the anchor would log unbounded.
- Stack status: `docker compose -f docker-compose.prod.yml ps`
- Deploy history: the repo's **Actions** tab, or the **Environments** widget on the repo homepage
  (populated by `deploy.yml`'s `environment:` key).
