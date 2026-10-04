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
   ├── /api/*, /openapi.json, /docs*, /redoc*  ──► 127.0.0.1:8000 (api container)
   └── everything else                          ──► static files, backend/frontend-dist/
       (figures and PDFs are under /api/v1 too, behind the session cookie — there is no /static/)

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
- `WORKER_MEM_LIMIT` (Compose default `7G`; `.env.prod` example `6G`) caps `celery_worker`'s memory so a huge PDF OOMs only that
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

### Embedding concurrency and interactive priority

`BULK_EMBEDDING_MAX_INFLIGHT=1` allows one bulk embedding request at a time across all API and
Celery processes sharing Redis. `BULK_EMBEDDING_BATCH_SIZE=4` sends at most four chunk texts in
each normal batch request. `EMBEDDING_MAX_CONCURRENCY` still controls the local worker thread pool;
every outbound bulk call must also acquire the shared Redis permit. The lease defaults to
`BULK_EMBEDDING_SEMAPHORE_TTL_S=360` seconds and renews while its process is alive. The setting
must remain longer than the synchronous embedding HTTP timeout of 300 seconds. If Redis is
unavailable while acquiring a permit, the worker logs a warning and uses a process-local semaphore.
Bulk embedding continues, so a permit-store outage alone does not fail ingestion. This fallback
does not preserve the cluster-wide limit: with `N` processes running bulk embedding and
`BULK_EMBEDDING_MAX_INFLIGHT=L`, up to `N × L` requests can run at once instead of the healthy
Redis-backed limit of `L`. `L` defaults to 1; the production Compose defaults allow two ingest and
two light-worker processes, so four bulk calls can overlap if all are busy. That can exceed the
recommended `OLLAMA_NUM_PARALLEL=2`, occupy Ollama's parallel slots, and queue interactive query
embeddings. If a query then exceeds its 8-second deadline, search uses the full-text fallback. Bulk
ingestion continues but may take longer. Treat the warning as degraded mode: monitor Ollama load
and search latency, and restore Redis to re-establish the shared limit.

Interactive query embeddings never acquire the bulk permit. Library search bounds each query
embedding call by `QUERY_EMBEDDING_TIMEOUT_S=8`; if it fails or times out, search logs the
degradation and uses English/Arabic full-text results.

The 2026-10-03 contention measurement recorded in the incident spec was 6.6–19.8 seconds for a
query embedding while one `embed_document` job was active; the 503s occurred while two jobs ran
at once. No new host measurement was made for this change. For host-side Ollama, the recommended
`OLLAMA_NUM_PARALLEL` value is `2`. Ollama runs on the host outside Compose, so apply that setting
there when configuring Ollama.

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

### What a restart does to a running ingestion

Every backend deploy recreates the worker (`up -d --build api celery_worker celery_worker_light`), and so does
autoheal after a hung health check. A task running at that moment — a MinerU extraction is
minutes long — dies with the process. Its message is `acks_late`, so Redis still holds it, but
Redis only re-delivers an unacked message after the visibility timeout (Celery's default: one
hour): the document sat at "extracting" for an hour, then started over (verified live
2026-09-12: a book pasted at 20:48, killed by the 20:50 deploy, still "extracting" at 21:08 with
its message in `unacked`). `core/celery_app.py::_restore_interrupted_tasks` now runs on
`worker_ready` and hands every unacked message straight back to the queue — the same thing kombu
does on a warm shutdown, which a container stop never gets for a long task. The interrupted
message is restored within seconds of the new worker coming up. Execution admission is
separate: the restored delivery is retained/requeued until the dead owner's outstanding
lease expires, at most 600 seconds (ten minutes) after its last renewal. Admission also
waits for surviving subprocess supervisors to terminate and reap their descendants;
this safety wait can outlast the lease. During this wait
the document still shows its last status (for example "extracting") and progress; the
progress bar restarts when extraction is admitted. This delay is expected, not a lost job.
Recovery is scoped to the starting worker's consumed queue, so starting one worker
does not restore the other worker's active tasks.

**The visibility timeout is now 7 days, and there are no task time limits — on purpose.** The
same one-hour re-delivery bit the other way on 2026-09-17: a 916-page book takes ~68 min to
extract, so at the 60-minute mark Redis handed the *still-running* task's message out again, and
with one worker at `--concurrency=1` the duplicate started the moment the first finished — the
same task id, ten times in a row, until the disk was full (§10). Anything longer than the longest
task there will ever be is the only correct value: a 10,000-page book is ~13 h of extraction
alone, its summaries longer. Seven days costs nothing, because a *dead* worker's messages come
back through the restore above rather than by waiting the timeout out, and no task here uses
`eta`/`countdown` (the one thing a long timeout breaks). A `task_time_limit` high enough not to
kill that book would be days — useless against a hang — so there is none; a hung worker is
autoheal's job (§4), a full disk the watchdog's (§10).

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

**Only what changed is rebuilt.** Every merge used to run the full sequence — `npm ci`, a Vite
build, `docker compose up -d --build`, an API container recreate — five and a half minutes and a
short API outage to ship a README fix. `scripts/deploy-scope.sh` now diffs the running commit
(`.last-good-sha`) against the one CI validated and the workflow passes the answer to
`deploy-once.sh` as `DEPLOY_SCOPE`: `none` (docs, tests, CI, samples — files synced, health
confirmed, nothing built or restarted), `frontend` (Vite build only; no container touched),
`backend` (api + both Celery workers rebuilt and restarted; the SPA untouched), `both`, or `full` (the
first deploy, or no diffable sha — and always the rollback path, since the failed attempt may have
rebuilt any subset). The rules mirror `ci.yml`'s "detect changes" filters, with two deliberate
differences: `backend/` means everything the images and their runtime config are built from
(`pyproject.toml`, `uv.lock`, the Dockerfiles, `docker/`, the compose file, the deploy scripts
themselves), not only `backend/app`; and `backend/nginx/` counts as *nothing*, because the host's
nginx config is installed by hand — a change there produces a `::notice::` in the run instead.
`npm ci` is skipped when `package-lock.json` is unchanged (its hash is kept in
`frontend/node_modules/.deployed-lock-hash`, and `node_modules` is excluded from the rsync's
`--delete` so it survives between runs). Measured: a frontend-only deploy is ~30 s; a docs-only
one is the health check.

The deploy job also builds the frontend in a throwaway `node:22-alpine` container with
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

## 9. Reclaiming disk without slowing the next deploy

What takes space on the box, in order: BuildKit's build cache (every backend rebuild leaves the
previous images' layers behind — ~4 GB per rebuild), then untagged `<none>` images from the same
rebuilds, then the two 9XAIPal images themselves (~11 GB; the worker alone is 9 GB of torch +
MinerU). Database volumes, the storage directory and container logs are small next to that.

### What a backend deploy costs, and what decides it

`deploy-once.sh` builds with `compose up -d --build`. Measured 2026-09-12 on this box:

| | fully cached | cache gone |
| --- | --- | --- |
| `apt-get` layer | 0 s | 20 s |
| `uv sync` (api / worker) | 0 s | 6 s / 32 s (with uv's download cache) · 34 s+ without |
| MinerU model download | 0 s | 24 s |
| exporting layers (9 GB worker image) | 0 s | 130–175 s |
| **api image rebuild, nothing changed** | **0.6 s** | **54 s** |

An `app/`-only change with a warm cache rebuilds one small `COPY` layer and exports that alone —
about a minute end to end. With the cache gone it is the full five minutes, on every backend
merge, whatever changed. Two things throw the cache away:

1. **`docker builder prune -f`** — what `deploy.yml`'s cleanup step ran after every deploy until
   2026-09-12. Without `--all` it is documented as removing only "dangling" cache, but under the
   containerd image store this daemon uses, BuildKit's step records are referenced by nothing once
   the build has finished, so plain `-f` reclaims them all (verified: prune, rebuild the unchanged
   api image → 0 of 6 steps cached, 54 s; skip the prune → 6 of 6 cached, 0.6 s). Every backend
   deploy since the step was added had been a cold rebuild. The step now prunes only records unused
   for a week and never cache mounts (`host/docker-prune.weekly` runs the same two commands from
   `/etc/cron.weekly` for the weeks in which nothing is deployed):

   ```bash
   docker image prune -f
   docker builder prune -f --filter until=168h --filter 'type!=exec.cachemount'
   docker volume ls -qf dangling=true | xargs -r docker volume rm   # manual only, see deploy.yml
   ```

   `until` is measured against *last use*, so a step reused by today's build is kept; growth is
   bounded because unchanged steps are reused, not duplicated — a new 2.7 GB `uv sync` record
   appears only when the lockfile changes, and the old one ages out in a week.

2. **The daemon's own builder GC** (`docker buildx inspect default`, "GC Policy rule#0"): with no
   `/etc/docker/daemon.json`, BuildKit caps `exec.cachemount` + `source.local` records at ~1.3 GiB
   and 48 h. The worker's uv download cache (the `--mount=type=cache` in `Dockerfile.mineru`) is
   ~1.7 GB, so BuildKit evicts it on its own shortly after every worker build — the cold `uv sync`
   is the default here, not an accident. This needs root: install `host/docker-daemon.json` as
   `/etc/docker/daemon.json`, then `sudo systemctl restart docker` (⚠ restarts every container on
   the box, the lcms stack included — a few seconds, but pick the moment; `systemctl reload
   docker` is **not** enough, builder GC is not among the options SIGHUP re-reads — verified
   2026-09-19: after a reload `buildx inspect` still showed the old numbers).

   ⚠ **`reservedSpace` is a floor, not a ceiling** — the single most misleading thing here, and
   what made the 40 GB in this file's earlier version useless. It (and its deprecated alias
   `keepStorage`/`defaultKeepStorage`) says "never prune *below* this", so on its own it bounds
   nothing: the cache had grown to 41.8 GB under a 40 GB `reservedSpace`, and to 30.3 GB under a
   20 GB one within a day of 2026-09-19's change. The ceiling is **`maxUsedSpace`**: GC triggers
   above it and prunes back down to `reservedSpace`. Both are set now — 18 GB floor / 28 GB
   ceiling overall, 6 GB / 10 GB for the cache mounts. The floor is sized from the measurement
   below it: a warm cache for both images is ~15.3 GB, so a floor under that would shave the warm
   set on every GC and hand back the cold rebuild this section exists to avoid. `docker buildx
   inspect default` afterwards should show rule #0 at 6 GiB reserved / 10 GiB max and rule #1 at
   18 GiB / 28 GiB; `dockerd --validate --config-file` checks the file before you install it.

### Do not

`docker system prune -a` / `docker builder prune -a` / `docker image prune -a`: `-a` also removes
the images the deploy pulls (`node:22-alpine`, `ghcr.io/astral-sh/uv`, `pgvector/pgvector`, …) and
every cache record including the uv mount, and the next deploy re-downloads all of it (this
happened on 2026-09-10 and 2026-09-12). `docker system df -v` shows what is left; the row of type
`exec.cachemount` is the one to keep.

## 10. The disk filling up: what happened on 2026-09-18, and what stops it now

The root disk hit 0 bytes free at 07:12 on 2026-09-18. Postgres PANICked on ENOSPC two minutes
later and stayed down; the worker died with it; rsyslogd and journald sat at 65 % CPU trying to
write. `df` said `/var/lib/containerd` was 78 of 96 GB — the containerd image store holds
*everything* Docker (§9), so `docker system df` is the map: build cache 41.8 GB, container
writable layers **32.6 GB**, images 16 GB. The 32.6 GB was one container, `9xaipal-celery-worker`,
and inside it `/app/output/<uuid>/` × 1,114 — each a copy of the same 28 MB PDF. Two bugs
compounding:

1. **Re-delivery loop.** The 916-page book above ran ten times back to back because its 68-minute
   task outlived the one-hour visibility timeout (§4, "now 7 days").
2. **MinerU's own copy of every request.** The MinerU API server keeps each upload and result
   under `MINERU_API_OUTPUT_ROOT`, default `./output` — `/app/output` in the worker, the
   container's writable layer, on no mount and cleaned by nothing. 115 page-batches per pass ×
   28 MB × 10 passes. The server now gets a scratch dir under `/data/storage/extracted/
   .mineru-api-*` and `_stop_mineru_api_server` removes it; `sweep_stale_scratch_dirs` removes
   any such dir (and `.<id>_batches`) still present when the worker starts, which is what a
   SIGKILL — deploy, autoheal, OOM — leaves behind (three were found on the first run).

What guards the disk now, innermost first:

- **The app refuses at 90 %.** `services/ingestion.py::check_disk_headroom` runs inside
  `check_queue_capacity` (so every accept path — upload, import, re-extract — gets it) and again
  at the top of `process_ingestion`, because a job accepted at 85 % can start after the one
  before it filled the disk. HTTP `507`, code `DISK_FULL`; in the worker the document is marked
  failed with the percentage in the message. Threshold: `ingestion_disk_refuse_percent`.
- **The host emails at 80 % and 90 %** — `host/disk-watchdog`, every 15 minutes from cron, with
  `df`, `docker system df`, the big directories and per-container layer sizes in the mail, a
  daily reminder while it stays there, one mail on recovery. Gmail SMTP on 587 (OVH blocks 25);
  install and settings in `host/README.md`.
- **Nothing accumulates on its own any more**: the 28 GB build-cache ceiling (§9), the weekly prune
  (`host/docker-prune.weekly`) for weeks without a deploy, and the scratch sweep above.
- **One job per document** (`JobAlreadyActive`, HTTP `409`, code `JOB_ACTIVE`), taken under the
  same advisory lock as the queue-capacity check. Required by `--concurrency=2`: a re-extract
  during an extraction would otherwise run both pipelines on the same `extracted/<id>/`, each
  wiping the other's chunks.
- **A deleted paper stops its own ingestion.** `DELETE /{paper_id}` removes the rows (job
  included, by cascade) and the files present at that moment, but until 2026-09-19 the running
  task carried on for up to an hour and re-created `extracted/<id>/` and `images/<id>/` for a
  paper that no longer existed. `pipeline_sync.run_pipeline_sync` now checks the row is still
  there before starting, after every page-batch (~40 s, through the progress callback:
  `ExtractionAborted` is the one exception `extract_pdf_sync` lets that callback raise) and before
  writing chunks; on `DocumentDeleted` it stops, the MinerU server's `finally` runs, and the task
  removes its own output.

Cleaning up by hand, if it ever comes to that: `journalctl --vacuum-size=200M` first (700 MB, and
it gives the daemons room to write again), then `docker builder prune -af` (this is the one time
`-a` is right: the disk is full and the next deploy being cold is the smaller problem), then find
the writable layer with `docker ps -s`. Postgres data is on a named volume and recovers on
`docker start`; the worker needs `docker compose up -d celery_worker` after Docker itself is
restarted, since dockerd's state is stale once it cannot write.

## Celery queue split

`celery_worker` consumes only `ingest` (`process_ingestion` and
`reconstruct_reading_order`). `celery_worker_light` uses the same worker image,
environment and storage, and consumes the light/default queue named `celery`
(article imports, embeddings, section summaries and figure descriptions). The
original `celery` name is retained so queued messages survive deployment.
Both Compose files assign `WORKER_ROLE=ingest` / `WORKER_ROLE=light`.
A heavy task delivered to light uses Celery `Task.replace` on `ingest` before
any database, disk or extraction work. The original task id, callbacks,
errbacks, chains and chord membership follow the replacement; no placeholder
success result runs continuations early. Publication can be duplicated after
an ambiguous broker reply. There is no Redis forwarding receipt to retain.

Heavy consumers claim Postgres rows before work: ingestion by job id,
reading-order reconstruction by document id. Execution state is separate from
pipeline progress. A token and a 600-second lease are renewed every 60 seconds
by a heartbeat thread, including during long OCR calls. A session advisory lock
also fences live owners across lease expiry. An independent watchdog terminates
a worker if renewal stalls for 120 seconds, well before its lease expires. Live-owner deliveries are rejected/requeued so a restored reservation remains recoverable
if its owner later dies. Finished delivery ids are logged, traced and acknowledged without
re-extraction. Distinct ingestion delivery ids replay the saved outcome for their own canvas.
Heavy tasks disable Celery's pre-body `STARTED` result write so a dropped
failed duplicate cannot overwrite the original failure; Postgres still reports
pipeline progress. Other task settings and retry budgets remain unchanged.
Heavy outcomes are checkpointed durably before returning to Celery; terminal
claims are committed in `after_return`, after canvas publication/result storage.
A crash in between replays the saved result or error without re-extraction,
then resumes callbacks/chains/chords/errbacks. Continuation publication remains
at least once across ambiguous broker replies; exactly-once Redis publication
is not claimed.
Every physical Redis receipt gets a fresh reservation tag before acknowledgment
bookkeeping. Restored or lost-reply copies retain their logical task/canvas IDs,
but a stale owner’s ACK cannot erase a newer reservation. Admission rejects
release their DB locks and pause 250 ms before requeueing, avoiding a hot loop
through new DB/TCP connections while an unfinished owner or lease is unchanged.
Finished ingestion delivery ids remain suppressed within a retry generation. Deliberate
Arabic confirmation advances the generation and clears ownership/outcome transactionally;
old-generation deliveries cannot execute the confirmed job. A distinct ingestion source
replays the saved result/error without heavy work. Completed reading-order delivery ids
remain suppressed; a fresh reading-order request is retained until the unfinished
prior delivery has executed or replayed its checkpoint and completed Celery
finalization. Pending retries also retain the original delivery’s ownership.
The newer request then executes without replacing the saved outcome.
A crashed owner's restored delivery is requeued until lease expiry, then
reclaims the job. Database admission/finalization failures retain the delivery;
Heavy and article tasks reject late-ack work on prefork child loss;
loss of heartbeat fencing terminates the worker process so it cannot keep
writing without ownership. Subprocesses spawned inside the heavy claim run through
Linux supervisors which observe the pool child with a pidfd, adopt orphaned
descendants as subreapers, and kill/reap the entire extraction tree on owner death.
A separate shared Postgres descendants lock prevents recovery until reaping
finishes, even after the original lease expires. The rollback overlay carries
the same supervisor. Late-ack recovery resumes the job after both fences permit it.

URL source deliveries hold a document advisory lock on the same pinned connection used
for every persistence statement across fetch, adoption and failure handling. SQL verifies
that physical connection still owns the lock; connection loss rejects/requeues the source
without failure cleanup or an HTML persistence tail. A concurrent delivery waits, then rechecks adoption, so
its delayed HTML response or fetch failure cannot clean up an adopted PDF.
PDF URL tasks commit adoption and use native `Task.replace` to transfer their id and
callbacks/errbacks/chains/chords to `process_ingestion` on `ingest`. The source cannot
complete its canvas before extraction. Direct pipeline callers publish the same document/job ids. Redelivery reuses the committed PDF before fetching or changing
status. Concurrent adoption locks the document row and preserves existing
bytes. An ambiguous publication requeues the article instead of failing an
already-running ingest job. HTML article processing stays on light.

`LIGHT_WORKER_CONCURRENCY` defaults to `2`; `LIGHT_WORKER_MEM_LIMIT` defaults to
`2G`. `WORKER_MEM_LIMIT` still caps the ingest worker (Compose defaults: `7G`
production, `12G` development). Production ingest concurrency stays `2`;
development keeps Celery's existing automatic concurrency. Backend/both deploys
build and update `api`, `celery_worker` and `celery_worker_light` together.

Startup recovery restores only messages for the restarting worker's queue;
only an ingest-role worker sweeps extraction scratch. Light never extracts
PDFs, including legacy or restored deliveries.

Automatic rollback runs `scripts/rollback-celery-queues.sh` from the new tree
before restoring the old revision: it stops autoheal, the API and both workers,
removes the introduced light container by name, and atomically moves every
`ingest` queue entry back to `celery` (including Redis priority buckets),
rewriting exchange and routing metadata to `celery`/`celery` so subsequent
restoration and retries remain on the old queue. It also
recovers reserved ingest messages from the broker's unacked hash/index, leaving
reserved default messages for the old worker's startup recovery. Message bodies,
headers, task ids and priorities are preserved. FIFO within each priority is
retained, and repeated helper
execution does not duplicate migrated work. The restored full deploy restarts
the stack; current full deploys also remove Compose orphans.

The helper removes the ingest exchange binding only after all queued and reserved messages have moved.

Rollback to a pre-split revision keeps a compatibility consumer: the workflow saves the
current claim/forwarding/compatibility modules outside the rsync target, restores the old
tree, then runs `scripts/prepare-celery-rollback.py` before rebuilding. The old worker
consumes `celery` with current Postgres fencing, outcome replay and retry generations;
its original extraction code stays in use. Adopted PDF source copies reuse the committed
file and replace onto `celery`. Queued and reserved duplicates remain safe, including
copies with different source ids/canvases and already-finished jobs. The API's deliberate
confirmation retry also retains generation advancement. Overlay failure aborts rollback
before any consumer starts. This is a compatibility rollback, not a byte-identical old tree.

URL PDF storage stages complete bytes privately, then serializes publication and
row adoption with a stable filesystem lock plus a checked Postgres row lock.
Committed PDFs remain immutable. When the row still describes an article,
canonical/raw files are uncommitted residue and both are replaced atomically
with the freshly fetched complete bytes, including truncated files left by
pre-split writers. The filesystem lock survives DB connection loss until the
stale publisher stops, so a successor cannot commit adoption before publication
is fenced. Raw storage uses the same bytes as the canonical PDF. Article failure
cleanup/status writes commit together; ownership/persistence failure requeues
the source instead of acknowledging the original fetch error. Abandoned private
stage files can remain after a crash; they are never admitted as canonical PDFs.

Compatibility rollback carries queue-only schema DDL into API migrations and checks it
before worker startup, even if the failed deployment never reached migrations. Schema
failure stops the worker. Queue-aware rollback revisions retain their separate roles,
current guards and native article handoff without wrapping guards twice. Early split
revisions also receive queue-scoped recovery and explicit roles. English/PDF extraction
functions are retained from the restored revision; only the URL adoption/handoff phase
is refreshed for queue-aware revisions.
