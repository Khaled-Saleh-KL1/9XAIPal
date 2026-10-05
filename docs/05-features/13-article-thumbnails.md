# Area 13 — Article thumbnails (feature 117)

> Part of the [feature catalogue](README.md). Operations: [article thumbnail runbook](../runbooks/article-thumbnails.md).
>
> **Reflects code as of:** 2026-10-05, checked against the linked backend and frontend files.

An imported article can have a generated portrait cover in the library. The image is an optional cache: article ingestion can complete without one, and an unset or invalid Cloudflare account configuration disables generation without failing the article job. The task and shared cover cache live in [`backend/app/workers/tasks.py`](../../backend/app/workers/tasks.py), [`backend/app/services/covers.py`](../../backend/app/services/covers.py), and [`backend/app/core/paths.py`](../../backend/app/core/paths.py).

## Generation flow

After article ingestion completes, the ingestion task makes a best-effort dispatch of `9xaipal.generate_article_thumbnail`. That task is routed to Celery's `celery` queue; the Compose `celery_worker_light` service consumes that queue. Dispatch failure is logged and does not undo completed ingestion. See [`tasks.py`](../../backend/app/workers/tasks.py), [`celery_app.py`](../../backend/app/core/celery_app.py), and the [development](../../backend/docker-compose.yml) and [production](../../backend/docker-compose.prod.yml) Compose files.

The task reads article text, builds a short image prompt, requests an image from Cloudflare Workers AI, converts it to a 480 × 621 JPEG, and atomically writes it to the shared covers directory as `<document-id>.jpg`. The covers directory is under `STORAGE_ROOT` (`/data/storage` in Compose). The shared path lets the API's cover endpoint serve the generated file through the existing cover route. See [`tasks.py`](../../backend/app/workers/tasks.py), [`cloudflare_images.py`](../../backend/app/services/cloudflare_images.py), [`paths.py`](../../backend/app/core/paths.py), and [`documents.py`](../../backend/app/api/v1/endpoints/documents.py).

## Prompt model and image model

The task calls the shared `chat_sync` chat path with a `system` message and the article text in a `user` message. The system instruction treats article text as untrusted subject matter and asks for one English visual description of at most 60 words. It avoids written material, logos, watermarks, and likenesses of real people. The result is normalized and capped so the final image prompt is at most 400 characters and retains the fixed style suffix. If the chat call fails, returns no usable description, or exceeds its 45-second wait, the task falls back to a title-based prompt; the fallback does not include the full article body. See [`tasks.py`](../../backend/app/workers/tasks.py).

Gemma's role is prompt writing when it is the configured chat model; it does not generate the image. The task is not hard-wired to Gemma: `chat_sync` uses the shared configured chat provider/model. The production Compose default is `CHAT_MODEL=gemma4:31b` with Ollama Cloud, while other deployment settings may route chat differently. Cloudflare Workers AI receives the composed visual prompt and returns image bytes. See [`tasks.py`](../../backend/app/workers/tasks.py), [`client.py`](../../backend/app/llm/client.py), [`resolver.py`](../../backend/app/llm/resolver.py), and [`docker-compose.prod.yml`](../../backend/docker-compose.prod.yml).

## Cloudflare account and model order

`CLOUDFLARE_AI_ACCOUNTS` is a comma-separated ordered list of `<account_id>:<token>` entries. Configuration keeps valid entries in that order; an account ID must be 32 lowercase hexadecimal characters and an entry without a token is ignored. `CLOUDFLARE_IMAGE_MODELS` is an optional comma-separated model list. If it is empty, the client uses its built-in ordered model fallback list in [cloudflare_images.py](../../backend/app/services/cloudflare_images.py). The list has eight entries in the current code and is not duplicated here. A custom comma-separated list replaces the built-in order.



The client is account-first: it tries each model, in model-list order, on the first eligible account before moving to the next configured account. A model-specific failure normally advances to the next model. HTTP 401/403 skips the rest of that account; a 429 or recognized quota response skips that account's remaining models because the quota is shared across its models, then tries the next account. Accounts with open circuit breakers are skipped. A successful image ends the cascade. There is no non-Cloudflare image-provider fallback. The implementation bounds one generation attempt to 180 seconds overall, with at most 60 seconds per request. See [`config.py`](../../backend/app/core/config.py) and [`cloudflare_images.py`](../../backend/app/services/cloudflare_images.py).

## Cache and `cover_version`

Generated article covers use the same document-ID keyed JPEG cache path as PDF cover images. A non-empty cached file makes the task idempotently return `exists`; the task does not overwrite it. Article cover files are installed by writing a temporary sibling file and replacing the destination atomically. See [`tasks.py`](../../backend/app/workers/tasks.py) and [`covers.py`](../../backend/app/services/covers.py).

The document list response computes `cover_version` only for articles with a non-empty cover: it is the integer file modification time, not a database counter. It is `null` until such a file exists and for non-article documents. The library passes that value into the cover URL as `?v=<mtime>` and resets its image-error state when the version changes, allowing the browser to request the newly generated cover after the list response observes it. See [`documents.py`](../../backend/app/api/v1/endpoints/documents.py), [`documents.py` schema](../../backend/app/schemas/documents.py), [`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx), [`PaperCover.tsx`](../../frontend/src/views/PaperCover.tsx), and [`api.ts`](../../frontend/src/api.ts).

## Retry and duplicate-work behavior

The task uses a Redis lease keyed by document ID, with a 10-minute expiry renewed every minute. Only the lease owner can renew or release it. The task checks for an existing non-empty cover before and after claiming the lease and again while holding the document row lock before writing. A duplicate ordinary delivery that finds an active lease returns `in_progress`; a worker-loss redelivery can be delayed once until just after the lease's maximum TTL. The Celery task acknowledges late and rejects work when its worker is lost. See [`tasks.py`](../../backend/app/workers/tasks.py).

Only the all-eligible-accounts-quota-exhausted condition gets an explicit Celery retry: up to three delayed retries, each scheduled for the next UTC 00:10 window. If those retries are exhausted, the task returns `quota_exhausted`. An all-open circuit returns `circuit_open`; other provider failures return `unavailable` or `failed` without the quota delay. A missing valid account list returns `disabled`. These outcomes do not fail an already completed article ingestion. See [`tasks.py`](../../backend/app/workers/tasks.py) and [`cloudflare_images.py`](../../backend/app/services/cloudflare_images.py).

Use the [runbook](../runbooks/article-thumbnails.md) for placeholder-only configuration, backfill options, and non-production verification.
