# Area 9 — Operations (features 101–107)

> Part of the [feature catalogue](README.md). Companions:
> [runtime-topology.md](../01-orientation/runtime-topology.md),
> [operations.md](../01-orientation/operations.md),
> [DEPLOYMENT-PRODUCTION.md](../../backend/DEPLOYMENT-PRODUCTION.md).
>
> **Reflects code as of:** 2026-09-12 (`main`, c099d90).

---

## 101. The Docker Compose stack

**What it does.** The whole backend on one box, self-contained: `api` (FastAPI, uvicorn
`--workers 2`, bound to `127.0.0.1:8000`), `celery_worker` (`--concurrency=1`, its own memory
limit), `postgres` (pgvector/pgvector:pg16, named volume), `redis` (persistent volume),
`autoheal`.

**Where.** [`backend/docker-compose.prod.yml`](../../backend/docker-compose.prod.yml) (production),
`docker-compose.yml` (development), `Dockerfile` / `Dockerfile.mineru` / `Dockerfile.lite`,
`uv.lock` (dependencies installed with `uv sync --locked`, the pinned `uv` binary copied from its
own image).

**How it works.** The `environment:` block of each service is an **allow-list**, not a
pass-through — a variable in `.env` that is not listed there never reaches the container (the
Semantic Scholar key sat in `.env` unseen until its line existed). `WORKER_MEM_LIMIT` caps the
worker so a huge PDF OOMs only that container, which restarts, instead of pressuring Postgres,
Redis and the API on the same box. Chat and vision run on Ollama Cloud (`OLLAMA_BASE_URL=
https://ollama.com`); embeddings run on an Ollama installed on the host, reached through
`host.docker.internal`. The image build uses a BuildKit cache mount for uv's download cache —
never `docker builder prune -a` on this box, or torch/MinerU re-download cold. ⚠ The box is not
single-tenant for Docker: an unrelated `lcms` stack runs alongside with its own Postgres/Redis,
started with plain `docker run` (no compose label), so daemon-wide prunes must be filtered by
`label=com.docker.compose.project=backend`.
⚠ **A restart used to lose the running ingestion for an hour** (2026-09-12): the task died with
the container, and Redis re-delivers an unacked message only after its one-hour visibility timeout.
`core/celery_app.py::_restore_interrupted_tasks` (on `worker_ready`) hands every unacked message
back to the queue at once, so a document interrupted by a deploy starts over within seconds. See
`DEPLOYMENT-PRODUCTION.md` §4.

---

## 102. LAN / single-port mode

**What it does.** `SERVE_FRONTEND=true` makes the API serve the built SPA at `/` itself, so one
port on a laptop turns it into a temporary server another PC or a tablet on the same Wi-Fi can
open — the tablet use case is why note dragging uses pointer events (feature 42).

**Where.** `core/lifecycle.py` (the SPA mount, decided at startup once volume state is stable, not
at module import), `backend/start-lan-server.sh`, `docs/01-orientation/setup.md § LAN`.

**Why the mount lives in lifespan.** With Docker and multiple workers the filesystem is not
guaranteed stable at import time; deciding after startup avoids one worker mounting a dist the
other cannot see.

---

## 103. Idempotent migrations

**What it does.** `schema.sql` is applied on every startup; every statement is `CREATE … IF NOT
EXISTS`; a `critical_alters` list of `ALTER TABLE … ADD COLUMN IF NOT EXISTS` recovers columns
added after a table was first created; pgvector's HNSW and full-text GIN indexes are
created/verified. No Alembic.

**Where.** [`database/migrations.py`](../../backend/app/database/migrations.py),
[`database/schema.sql`](../../backend/app/database/schema.sql), `database/pgvector.py`.

**How it works.** `_strip_line_comments` runs before the file is split on `;` — a semicolon inside
an ordinary prose comment has **twice** desynced the naive split, turning the comment into the tail
of the previous statement and the next `CREATE TABLE` into invalid SQL that failed silently, taking
every later statement with it. Each recovery statement runs in **its own transaction**: Postgres
aborts the whole transaction after one failed statement, so a single failed repair used to disable
every later independent one ([009](../issues/009-migration-recovery-uses-an-aborted-transaction.md)).

**Why.** A personal deployment that must come up clean on any box, including one whose schema was
created months ago, without a migration history to keep in step.

---

## 104. CI and automatic deploy with rollback

**What it does.** Every PR runs CI (backend suite against a real Postgres + Redis service
container, frontend `tsc` + `vite build`, a change detector that skips what didn't change). A
merge to `main` deploys to the box **only after CI for that exact commit succeeds**, and a deploy
whose health check fails rolls back to the last good commit automatically.

**Where.** [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml),
[`deploy.yml`](../../.github/workflows/deploy.yml), [`scripts/deploy-once.sh`](../../scripts/deploy-once.sh),
`~/apps/9xaipal/.last-good-sha`, branch protection requiring the `backend` and `frontend` checks.

**How it works.** The self-hosted runner lives on the production box. `deploy.yml` fires on
`workflow_run` of CI, not on the push: the two used to be independent workflows on the same event
with no ordering, so a push that failed CI would still deploy, and a direct push bypassing a PR
would deploy with zero verification. One deploy target, so runs queue instead of overlapping
(never kill a deploy mid-rebuild). The workflow rsyncs tracked files one-way into `~/apps/9xaipal`
(untracked files like `.env` and `.last-good-sha` are excluded — which is why the deploy target is
*not* a checkout and edits there are reverted on the next deploy), then `deploy-once.sh`: build the
frontend in a `node:20-alpine` container with `VITE_API_BASE_URL` set, copy `dist/` into
`backend/frontend-dist`, `docker compose up -d --build`, poll `/api/v1/health` for 60 s. On success
the commit is written to `.last-good-sha`; on failure the workflow checks out that sha into a
worktree and runs **the exact same script** again — one script, one place a deploy bug can hide.

**Only what changed is rebuilt.** `scripts/deploy-scope.sh` diffs `.last-good-sha` against the new
commit and the workflow passes `DEPLOY_SCOPE` to the script: `none` for docs/tests/CI (files
synced, health confirmed, nothing built or restarted), `frontend` (Vite build only, no container
touched), `backend` (api + worker rebuilt and restarted), `both`, `full` (first deploy, undiffable
sha, and always the rollback). `backend/` means everything the images are built from — lockfile,
Dockerfiles, compose, the deploy scripts — not just `backend/app`; `backend/nginx/` counts as
nothing because the host config is hand-installed, and produces a notice instead. `npm ci` is
skipped when the lockfile hash matches the last install. Before this, every merge — a README fix
included — cost five and a half minutes and a short API outage.

**The cleanup step, and the trap in it.** After every deploy the workflow prunes dangling images,
week-old build cache and this project's dead containers/volumes (label-filtered: the box also
runs an unrelated `lcms` stack). ⚠ Until 2026-09-12 that step ran `docker builder prune -f`,
which under the containerd image store reclaims the just-built images' step records — so every
backend deploy was a cold rebuild (apt-get + uv sync + MinerU models + 130–175 s exporting the
9 GB worker image ≈ 5 min) whatever had changed; a fully cached api rebuild is 0.6 s. The prune
now keeps anything used within a week and never touches the uv cache mount. The daemon's own GC
also caps cache mounts at ~1.3 GiB by default, below the worker's ~1.7 GB uv cache — a
`daemon.json` change, see `DEPLOYMENT-PRODUCTION.md` §9.

---

## 105. nginx same-origin serving

**What it does.** The public entrypoint: nginx on the host serves the SPA from
`backend/frontend-dist` and proxies `/api/*`, `/openapi.json`, `/docs*`, `/redoc*` to
`127.0.0.1:8000`; certbot manages TLS for `*.kl1.site`.

**Where.** [`backend/nginx/9xaipal.conf`](../../backend/nginx/9xaipal.conf) (the repo copy) →
`/etc/nginx/sites-enabled/9xaipal.conf` on the host (needs sudo).

**How it works, and the traps it encodes.** API routes are matched *before* the SPA catch-all, or
they silently return `index.html` with HTTP 200 — a success to curl and a broken image to a
browser. `proxy_buffering off` + `chunked_transfer_encoding on` + a 600 s read timeout keep SSE
streams flushing to the browser instead of sitting in a buffer. `.mjs` gets an explicit
`text/javascript`: nginx's bundled `mime.types` often has no entry, and a browser refuses to run a
module script (pdf.js's worker) served as `application/octet-stream` — the viewer stays blank with
no visible error. `Cache-Control: no-cache` on `index.html` because each deploy wipes and replaces
every content-hashed asset; a back/forward navigation served from cache would reference bundles
that no longer exist. `client_max_body_size 500m` mirrors `MAX_UPLOAD_SIZE_MB` — nginx rejects a
larger body with its own bare 413 page before the API sees it. There is deliberately no
`/static/` route any more (feature 91).

**Why same-origin.** The SPA calls `/api` on the host it was served from: no CORS preflight, no
mixed content, and the raw-snapshot iframe can be read for scroll sync (feature 3).

---

## 106. Docs discipline

**What it does.** The repository documents itself to a standard: every `docs/` file carries a
"Status / Last verified" header naming what it was checked against; `docs/roadmap.md` owns known
gaps (an entry is struck through and dated, never deleted); non-trivial work gets a design note in
`docs/plans/` (§-structured: the premise, the mechanism, what was verified, what is deliberately
not done); audit findings live in `docs/issues/` with severity, trigger, root cause and
resolution; dense `⚠` comments in the code explain *why*, including what was tried and failed.

**Where.** `docs/README.md`, `docs/01-orientation` (setup, operations, topology),
`docs/02-architecture` (overview, ingestion, chat-and-ask, ai-backend, auth, frontend),
`docs/03-reference` (api, configuration, database-schema, migrations, storage),
`docs/04-testing/test-plan.md`, `docs/plans/`, `docs/issues/`, `docs/decisions.md`, this catalogue.

**Why.** The comments are the institutional memory of a one-person project: the three iterations
of the reader's corner button, the two versions of sub/sup repair that were not enough, the Google
providers that were tried and removed. A reader (or an agent) who does not know why a thing is the
way it is will "fix" it back.

---

## 107. The test suite

**What it does.** 613 backend tests (`backend/tests/`) run against a real Postgres + Redis, plus
render tests for the UI: `renderToString` for every state of the export wizard, the evidence
panel and the citation chips, and real-DOM (happy-dom + react-dom) behaviour tests for the book
reader's reveal, retry and reading-order and for the citation queue.

**Where.** `backend/tests/` (`conftest.py`, `pytest.ini`), `backend/tests/README.md`,
`docs/04-testing/test-plan.md`, CI's `backend` job.

**How it works.** ⚠ **Every test truncates `documents CASCADE`** — chunks, assets, conversations,
notes go with it. `conftest.py` refuses to start unless `POSTGRES_DB` contains "test"
(`ALLOW_DESTRUCTIVE_TESTS=1` overrides), which is why the suite is never run against the live box's
database; on the VPS it runs against a throwaway `pgvector/pgvector:pg16` + `redis:7-alpine` pair
on the compose network with the production image and the checkout's `app/` mounted. The
HTTP-layer tests use `httpx.ASGITransport` with `DEBUG=true` (the session cookie is `Secure` unless
debug, and a plain-HTTP test client cannot see a `Secure` cookie). Concurrency-critical modules
(capacity, the pacer) test against real Redis rather than a mock — their correctness is about how
the commands compose, which a mock would only restate.

**Why the render tests exist.** The clickable-citations regression shipped because `tsc` and a
build passed while the component threw on first render; the export wizard's first version sent
zero requests to the API. A component split into a pure render and a thin stateful wrapper can
have every state asserted without clicking through it.
