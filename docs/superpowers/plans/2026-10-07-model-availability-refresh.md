# Scheduled Model Availability Refresh Implementation Plan

> **For agentic workers:** Execute inline in the specified worktree. The supplied M4 spec authorizes proceeding without another approval step. Work only in `/Users/khaled_saleh_kl1/MyStuff/All_Programming_Files/9XAIPal_VPS-probe`, and follow the spec's test and commit constraints.

**Goal:** Refresh cached per-model availability every three hours and once shortly after the light Celery worker starts, without changing routing or picker behavior.

**Architecture:** Add two validated settings and a conditional beat entry. A light-worker startup signal queues one refresh after 60 seconds. A synchronous Celery task obtains a Redis `SET NX EX` lease, discovers chat models from Ollama tags and configured provider pins, probes at most three models concurrently with a 45-second per-model deadline, and writes only definitive results through `record_model_result_sync`. NVIDIA probes use resolver key rotation and its shared rate limiter.

**Tech Stack:** Python 3.11+, Pydantic Settings, Celery, Redis, httpx, pytest, Docker Compose.

**Spec:** `/private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/spec-M4.md`

## Global Constraints

- Never write secrets. Never push, ssh, or contact production or external APIs.
- Never restart containers that were not started for this task.
- Do not change routing, fallback, or picker behavior; the task only feeds the cache.
- Keep the availability TTL at six hours and schedule refresh every three hours by default.
- Use mock HTTP and real disposable Redis for refresh tests.
- Run matching backend tests against throwaway Postgres and Redis on ports 55471/55472, then remove both task containers.
- Commit on `feat/model-availability-probe`; every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Write the requested report to `/private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/report-M4.md`.

## Review Focus

- Ollama tags unavailable or malformed: Ollama probes are skipped while configured provider pins can still be probed; pin with tests in Task 2.
- NVIDIA provider is unconfigured: its pin stays absent as in the catalog, with no HTTP request or cache write; test in Task 2.
- HTTP 500, other non-definitive status, and timeout: cache is left unchanged; test in Task 2.
- Concurrent model list grows: no more than three model requests are in flight; test in Task 2.
- Light worker starts while the refresh is disabled or on the ingest role: no startup run is queued; test in Task 1.

---

### Task 1: Configuration and light-worker scheduling

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/celery_app.py`
- Modify: `backend/app/workers/reliability.py`
- Modify: `backend/.env.example`
- Modify: `backend/.env.prod`
- Modify: `backend/docker-compose.yml`
- Modify: `backend/docker-compose.prod.yml`
- Test: `backend/tests/test_celery_queues.py`
- Test: `backend/tests/test_compose_env_example.py`

**Interfaces:**
- Produces `settings.enable_model_availability_refresh: bool` (default `True`) and `settings.model_availability_refresh_hours: int` (default `3`, greater than zero).
- Produces `_build_beat_schedule()` in `app.core.celery_app`; it retains existing entries and conditionally registers task `9xaipal.refresh_model_availability` on queue `celery` at the configured hours converted to seconds.
- Produces a worker-ready handler in `app.workers.reliability` that queues the refresh with `countdown=60` only when the feature is enabled and `WORKER_ROLE=light`.

- [ ] Write tests for the configured interval, disabled schedule, light-role startup countdown, non-light role, and disabled startup.
- [ ] Run those tests and verify they fail because the requested configuration and schedule do not exist yet.
- [ ] Add the settings, schedule factory, worker-ready handler, env defaults, and light-worker Compose settings.
- [ ] Run the focused tests and verify the conditional schedule, startup dispatch, and Compose defaults pass.

### Task 2: Locked concurrent provider probes

**Files:**
- Modify: `backend/app/workers/reliability.py`
- Test: `backend/tests/test_model_availability_refresh.py`

**Interfaces:**
- Produces Celery task `9xaipal.refresh_model_availability` on queue `celery`.
- Discovers non-embedding Ollama tags from `/api/tags` using `resolver._ollama_headers()` and configured `resolver.MODEL_PROVIDER_PINS` using the resolver's NVIDIA key rotation.
- Uses a Redis lease acquired with `SET NX EX`, at most three concurrent HTTP requests, 45 seconds maximum per model, `resolver.throttle_nvidia_key`, and `availability.record_model_result_sync`.
- Returns and logs only available, unavailable, and skipped counts; never logs credential values.

- [ ] Add real-Redis tests with mocked HTTP for HTTP 402 plus reason, HTTP 200, HTTP 500 and timeout with no cache update, overlap lock, model-source filtering, provider headers/payloads, and concurrency at most three.
- [ ] Run the new tests to verify they fail because the task does not exist yet.
- [ ] Implement discovery, per-provider probe requests, lock acquisition/renewal/release, result recording, startup dispatch target, and one summary log line.
- [ ] Run the new tests and verify their real Redis observations and mocked request assertions pass.

### Task 3: Model availability documentation and complete verification

**Files:**
- Modify: `docs/05-features/08-models-and-configuration.md`
- Test: all `backend/tests/` files matching `availability|catalog|reliability|celery|beat|compose|env_example`, plus `backend/tests/test_model_availability_refresh.py`.

- [ ] Update the `/models` documentation to explain that real chat outcomes and scheduled probes refresh the six-hour cache, including the three-hour cadence and transient-error handling.
- [ ] Start only the prescribed disposable Postgres and Redis containers if their names and ports are free.
- [ ] Run the matching backend tests natively using the documented macOS lockfile workaround if needed; set `INGESTION_DISK_REFUSE_PERCENT=101` for tests only if the disk guard blocks unrelated tests.
- [ ] Remove both task containers after tests.
- [ ] Review the complete diff, commit the implementation with the required co-author trailer, and write report-M4.md with file/line references and test tails.
