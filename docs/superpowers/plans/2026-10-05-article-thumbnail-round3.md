# Article Thumbnail Round 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the 45-second article-thumbnail prompt bound inside the thumbnail task while restoring shared LLM clients to `origin/main`.

**Architecture:** Submit the existing `chat_sync(...)` call without new arguments to one module-level `ThreadPoolExecutor(max_workers=2)` and apply `future.result(timeout=45)`. Timeout and other exceptions select the existing deterministic title-based fallback; the worker thread is not joined. Restore the shared LLM client files and remove only the test that exercises their reverted deadline parameter.

**Tech Stack:** Python 3.11, `concurrent.futures`, Celery, pytest, Docker test runner.

**Spec:** `/private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/spec-L6d.md`

## Global Constraints

- Never push, SSH, contact production, call external APIs, or restart containers.
- Do not change shared LLM client code; both client files must be byte-identical to `origin/main`.
- Use `chat_sync(...)` with no new parameters and enforce the deadline only inside the thumbnail task.
- Log fallback using no article text or tokens.
- Run the exact focused Docker test command from the spec.
- Commit with a message ending in `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Write the final report to the exact scratchpad path named in the spec.

## Review Focus

- A blocked model call returns to thumbnail generation within a short patched timeout and the unfinished worker is released by the test.
- An arbitrary model-call exception uses the title fallback and sensitive exception text is absent from logs.
- A successful model response contributes its description to the image prompt and `chat_sync` receives no timeout parameter.

---

### Task 1: Bound article-thumbnail prompt generation locally

**Files:**
- Modify: `backend/app/workers/tasks.py`
- Modify: `backend/tests/test_article_thumbnail_task.py`
- Restore from `origin/main`: `backend/app/llm/client.py`, `backend/app/llm/ollama_client.py`, `backend/tests/test_llm_client_cascade.py`

**Interfaces:**
- The thumbnail task owns a shared module-level `ThreadPoolExecutor(max_workers=2)` and a timeout constant patched to `0.2` seconds by the hanging-call test.
- `_article_thumbnail_prompt(article_text)` keeps its existing interface and fallback behavior.

- [x] Add a blocking `threading.Event` regression test; assert the task falls back within the patched timeout and generates the cover.
- [x] Add a raising mock regression test; assert title-based fallback and verify exception text is not logged.
- [x] Strengthen the normal path test to assert the model output is present in the generated prompt and no timeout argument is passed.
- [x] Run the three tests and confirm the wall-clock timeout test fails against the current direct `chat_sync` call.
- [x] Restore both shared LLM client files and the cascade test file from `origin/main`.
- [x] Implement the executor submission and `future.result(timeout=...)` handling in the thumbnail task; keep fallback logs free of article text and tokens.
- [x] Run the three regression tests and the exact focused Docker command from the spec.
- [x] Confirm `git diff origin/main -- backend/app/llm` is empty.
- [x] Commit the implementation with the required co-author trailer.
- [x] Write the report to the exact path required by the spec.
