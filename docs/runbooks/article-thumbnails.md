# Article thumbnail generation runbook

> **Status:** Source-checked 2026-10-05. This runbook describes code and Compose wiring; no production service was contacted.

This runbook covers generated covers for `doc_kind='article'`. The thumbnail is optional: article ingestion dispatches its generation as best effort, and without a valid Cloudflare account the thumbnail task returns `disabled` while the article stays complete. The task, client, config parsing, and queue wiring are in [`tasks.py`](../../backend/app/workers/tasks.py), [`cloudflare_images.py`](../../backend/app/services/cloudflare_images.py), [`config.py`](../../backend/app/core/config.py), and [`celery_app.py`](../../backend/app/core/celery_app.py).

## Data flow and service queue

The chat model receives the article text to write a visual description; Cloudflare Workers AI receives the composed image prompt. With the production Compose defaults, chat uses the configured `CHAT_MODEL` (default `gemma4:31b`) through Ollama Cloud. Gemma writes the text prompt; Cloudflare generates the image. This is a provider call carrying article-derived content, so enable it only where that data flow is acceptable. See [`tasks.py`](../../backend/app/workers/tasks.py), [`resolver.py`](../../backend/app/llm/resolver.py), and [`docker-compose.prod.yml`](../../backend/docker-compose.prod.yml).

Thumbnail work is sent to Celery's `celery` queue. The `celery_worker_light` service consumes it in both Compose configurations; the ingest worker consumes the separate `ingest` queue. The Cloudflare variables are included in the API environment and worker environment blocks, and the light worker inherits the worker environment. If the values change, ensure the running task consumer receives the updated environment. See [`celery_app.py`](../../backend/app/core/celery_app.py), [`docker-compose.yml`](../../backend/docker-compose.yml), and [`docker-compose.prod.yml`](../../backend/docker-compose.prod.yml).

## Configuration

Supply account tokens through the environment used by Compose or a secret store; do not commit tokens or print them in logs or command output. Compose forwards these variables explicitly, so values must be present in the environment supplied to Compose and in the relevant service environment. Use placeholders in documentation and examples only:

```dotenv
CLOUDFLARE_AI_ACCOUNTS=<account_id>:<token>
CLOUDFLARE_IMAGE_MODELS=<model-id>[,<model-id>...]
```

`CLOUDFLARE_AI_ACCOUNTS` accepts comma-separated entries in failover order. Each entry needs an account ID matching 32 lowercase hexadecimal characters and a non-empty token; invalid entries are ignored. With no valid account, the task makes no image request. `CLOUDFLARE_IMAGE_MODELS` is optional; a blank value uses the ordered fallback list defined in the client; see [the feature description](../05-features/13-article-thumbnails.md#cloudflare-account-and-model-order). A custom list replaces the defaults, and its order is honored. See [`config.py`](../../backend/app/core/config.py), [`cloudflare_images.py`](../../backend/app/services/cloudflare_images.py), and the [Compose environment blocks](../../backend/docker-compose.prod.yml).

### Failover behavior

The order is account-first, then model order within each account. A network error, non-quota model error, invalid image response, or oversized response advances to the next model where possible. Authentication rejection skips the remaining models for that account and advances to the next account. A 429 or recognized quota response skips the remaining models for that account and advances to the next account because the account quota is shared across models. Open account circuit breakers are skipped. A successful image stops the cascade; if every configured account is breaker-open, the task returns `circuit_open`. There is no secondary image provider. One call has a 180-second total deadline and 60-second per-request timeout. See [`cloudflare_images.py`](../../backend/app/services/cloudflare_images.py) and [`tasks.py`](../../backend/app/workers/tasks.py).

## Backfill

The backfill queues only documents that are complete articles and have no non-empty cached cover. It orders candidates by creation time and ID. `--limit N` caps the number selected and must be at least 1; `--user <uuid>` filters to one owner; `--dry-run` reports the eligible count without enqueueing. The dry run still opens the configured database and runs the candidate query, so it is not a no-contact check and must only be used with an intended non-production database during verification. There is no `--force` option; an existing non-empty cover is skipped. See [`backfill_article_thumbnails.py`](../../backend/scripts/backfill_article_thumbnails.py).

The script documents these invocations from a backend container's `/app` working directory:

```bash
python scripts/backfill_article_thumbnails.py --dry-run
python scripts/backfill_article_thumbnails.py --limit 100 --user <uuid>
```

The production API image uses [`Dockerfile.lite`](../../backend/Dockerfile.lite), whose `/app` working directory and `COPY scripts ./scripts` make the script available there; the local API image in [`Dockerfile`](../../backend/Dockerfile) does not copy `scripts/`. The script only enqueues tasks; image generation runs later on the `celery` queue consumer. The backfill does not contact Cloudflare synchronously. See [`docker-compose.prod.yml`](../../backend/docker-compose.prod.yml) and [`celery_app.py`](../../backend/app/core/celery_app.py).

## Retry and idempotence

The task retries only the case where every eligible Cloudflare account confirms quota exhaustion: at most three Celery retries, each scheduled for the next UTC 00:10 window. After that it returns `quota_exhausted`. All-open circuit breakers return `circuit_open`; ordinary provider failures return `unavailable`/`failed` rather than entering the quota schedule. Worker-loss delivery is configured with late acknowledgement and reject-on-worker-lost. A per-document Redis lease lasts 10 minutes and renews every minute; a redelivery that finds a stale lease waits once for 10 minutes plus one second before retrying. See [`tasks.py`](../../backend/app/workers/tasks.py) and [`cloudflare_images.py`](../../backend/app/services/cloudflare_images.py).

Existing non-empty covers are not regenerated by a repeated backfill. Concurrent duplicates normally stop at the existing-cover check or the lease. The task rechecks the document under a row lock before installing an image, then writes a temporary JPEG and atomically replaces the cache file. If the article was deleted before installation, it returns `deleted`. See [`tasks.py`](../../backend/app/workers/tasks.py).

## Safe verification without production or provider calls

Use the focused tests in [`test_article_thumbnail_task.py`](../../backend/tests/test_article_thumbnail_task.py), [`test_cloudflare_images.py`](../../backend/tests/test_cloudflare_images.py), and [`test_backfill_article_thumbnails.py`](../../backend/tests/test_backfill_article_thumbnails.py). They replace the chat/image calls, Cloudflare HTTP exchange, database/session access, lease handling, and task enqueueing with fakes or mocked transports; the task test writes only to a temporary directory.

The test suite has an autouse fixture in [`tests/conftest.py`](../../backend/tests/conftest.py) that applies migrations and truncates `documents`, `studies`, `sticky_notes`, and `users` for every test. Run tests only against a dedicated disposable local test database and local dependencies. The `POSTGRES_DB` name containing `test` is only a guard; also verify that the configured database host is the isolated local test service. Never set `ALLOW_DESTRUCTIVE_TESTS=1` as a shortcut, and never point tests or a backfill dry run at production.

For an operational dry run, first confirm the process is configured for an isolated non-production database. Remember that `--dry-run` still queries that database even though it does not queue Celery tasks. No live image generation is needed to verify prompt shaping, failover decisions, output conversion, locking, cache idempotence, or backfill selection; the focused mocked tests exercise those paths.
