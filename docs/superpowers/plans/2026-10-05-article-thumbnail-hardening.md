# Article Thumbnail Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address all five review findings for article thumbnail generation without contacting external services or production.

**Architecture:** Bound Cloudflare response reads to 8 MiB, filter breaker-open accounts strictly, and apply a 180-second deadline across all account/model attempts. Claim a 10-minute Redis lease before paid prompt/image generation, release it only when its owner token still matches, and reject malformed Cloudflare account IDs before URL construction.

**Tech Stack:** Python 3.11+, httpx, redis-py, Celery, pytest, Vitest, Vite.

**Spec:** `/private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/spec-L6b.md`

## Global Constraints

- Never push, SSH, contact production, call external APIs, or restart containers.
- Do not run the full backend suite; run the focused backend command in the spec, `npx vitest run`, and `npm run build`.
- Keep the existing no-global-task-time-limit policy; the Cloudflare model cascade itself has a 180-second total deadline.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Write the final report to the exact scratchpad path named in the spec.

## Review Focus

- Oversized chunked response: stop reading once the 8 MiB cap is crossed; bound encoded JSON image data before base64 decoding (Task 1).
- Breaker states: skip one open account while trying a healthy one, and schedule the existing delayed retry when all are open (Task 2).
- Deadline edge: stop before issuing another model request after the shared deadline expires (Task 3).
- Lease ownership: a stale owner must not delete a later owner's Redis lock (Task 4).
- Invalid account IDs: reject short, uppercase, and path-like IDs, and never put the associated token in the warning (Task 5).

---

### Task 1: Stream and cap Cloudflare image responses

**Files:**
- Modify: `backend/app/services/cloudflare_images.py`
- Test: `backend/tests/test_cloudflare_images.py`
- Include this plan in the task commit.

**Interfaces:**
- Produces `_MAX_RESPONSE_BYTES = 8 * 1024 * 1024`, `_MAX_JSON_IMAGE_BYTES`, and bounded response reading used by `generate_image`.
- Uses `httpx.stream` instead of eager `httpx.post`; preserve existing parsed image/error behavior for bodies at or below the cap.

- [x] Add a streaming test whose byte stream crosses 8 MiB and assert the reader closes without consuming later chunks and returns no image.
- [x] Add a direct JSON-image helper test proving an encoded value over `_MAX_JSON_IMAGE_BYTES` returns `None` before `base64.b64decode` runs.
- [x] Run the two new focused tests and confirm the expected failures.
- [x] Implement capped streamed reads in 64 KiB chunks; cap encoded JSON before decoding.
- [x] Run the new tests plus existing raw-image and JSON-image tests; commit with the required trailer.

### Task 2: Strictly skip open account breakers

**Files:**
- Modify: `backend/app/services/cloudflare_images.py`
- Test: `backend/tests/test_cloudflare_images.py`

**Interfaces:**
- Consumes Task 1's bounded stream request path.
- Builds eligible account indexes using `circuit_breaker.is_open` and raises `QuotaExhaustedError` when no configured account is eligible, allowing the task's existing delayed retry.

- [x] Add tests for one open account plus one healthy account, and for every account breaker open; assert open accounts receive no request and all-open raises `QuotaExhaustedError`.
- [x] Run those tests and confirm they fail against the current fallback behavior.
- [x] Implement strict filtering locally without changing the shared circuit-breaker behavior used by other providers.
- [x] Run the new and existing Cloudflare failover tests; commit with the required trailer.

### Task 3: Bound total account/model attempts

**Files:**
- Modify: `backend/app/services/cloudflare_images.py`
- Test: `backend/tests/test_cloudflare_images.py`

**Interfaces:**
- Uses `_TOTAL_ATTEMPT_TIMEOUT_SECONDS = 180.0` and `_TIMEOUT_SECONDS = 60.0`.
- Before each request, passes `min(_TIMEOUT_SECONDS, remaining_deadline)` as the request timeout; stops when no time remains.

- [x] Add a deterministic-clock test where the deadline expires after two attempts; assert request timeouts shrink to the remaining time and no later model is requested.
- [x] Run it and confirm the current implementation exceeds the total budget.
- [x] Implement the shared monotonic deadline across every configured account and model.
- [x] Run the deadline, account failover, and quota tests; commit with the required trailer.

### Task 4: Claim a per-document Redis lease before generation

**Files:**
- Modify: `backend/app/workers/tasks.py`
- Test: `backend/tests/test_article_thumbnail_task.py`

**Interfaces:**
- `_claim_article_thumbnail(document_id: UUID) -> str | None` uses `SET key token NX EX 600`; `None` means another task owns the lease.
- `_release_article_thumbnail(document_id: UUID, token: str)` compare-deletes with Lua so an expired owner cannot release a replacement lease.
- Return `status: in_progress` on contention; acquire before `chat_sync` and image generation; release in `finally`.

- [x] Add a task test where claim returns `None`; assert it exits as `in_progress` without calling the prompt model or image generator.
- [x] Add helper tests for `NX`/`EX=600`, successful claim, and compare-delete preserving a different token.
- [x] Run these tests and confirm failure before implementation.
- [x] Implement the synchronous Redis claim/release helpers and release the lease on every exit after acquisition.
- [x] Run the new tests and existing thumbnail task tests; commit with the required trailer.

### Task 5: Validate Cloudflare account IDs without logging tokens

**Files:**
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_cloudflare_images.py`

**Interfaces:**
- `Settings.cloudflare_ai_accounts` accepts only account IDs matching `^[0-9a-f]{32}$`.
- Ignore invalid IDs and emit a warning containing neither the token nor the raw configuration entry.

- [x] Add tests for valid lowercase hex, short, uppercase, and path-like IDs; assert invalid entries are omitted and their token is absent from captured warnings.
- [x] Run those tests and confirm the current parser accepts malformed IDs.
- [x] Implement regex validation and a token-safe warning; update existing fixture IDs to valid 32-character lowercase hex.
- [x] Run the focused config and Cloudflare tests; commit with the required trailer.

## Final verification

- Run the exact focused backend Docker command from the spec (no full backend suite).
- Run `npx vitest run` and `npm run build` in `frontend/`.
- Review the diff and commit history; write the report to `/private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/report-L6b.md`.
