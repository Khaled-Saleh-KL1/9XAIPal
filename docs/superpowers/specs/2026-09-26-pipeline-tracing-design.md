# Pipeline Tracing Design

**Date:** 2026-09-26

**Status:** Design approved in conversation; written specification awaiting review

## 1. Objective

Give the owner a Dify-style view of every request 9XAIPal handles: each chat
question and each ingestion (paper, book, article, URL) appears as one trace,
a tree of blocks in call order. Opening a block shows what it received, what
it did, what it produced, how long it took, and whether it failed.

The site is for the owner to debug and understand the system. Users never see
it. It is served at `https://9xaipal-trace.kl1.site`.

## 2. Agreed decisions

1. **Domain:** `9xaipal-trace.kl1.site`. The originally requested
   `9xaipal_trace` cannot receive a public TLS certificate because hostnames
   with underscores are rejected by certificate authorities.
2. **Detail level:** full content — real prompts, model answers, retrieved
   chunk text, search results and extracted text — truncated to a size cap,
   visible only behind the trace site's login, deleted automatically after the
   retention period.
3. **Instrumentation scope:** recording wrappers are allowed throughout the
   code, including the English ingestion and chat paths, on two conditions:
   what the code computes must not change (proved by the main-vs-branch
   differential check, section 9), and a `TRACE_ENABLED=false` kill switch
   turns all recording off.
4. **Approach:** OpenTelemetry instrumentation in the app, with self-hosted
   Arize Phoenix as the viewer. Rejected: a custom viewer built into the app
   (the whole UI would be new code), and Langfuse or Opik (their ClickHouse,
   Redis and object-store stacks need roughly 2–4 GB of RAM, competing with
   MinerU on the 11 GB production host).

## 3. Scope

### Included

- Chat: the `/ask` orchestrator, routing, context building, retrieval, web
  search, agent tool loops, every LLM call, persistence.
- Ingestion: queue wait, every Celery ingestion task in the chain, extraction
  (MinerU, VLM, PyMuPDF fallback, Arabic OCR), chunking, glyph and heading
  repair, asset handling, persistence, embeddings, section summaries, figure
  descriptions, reading-order reconstruction.
- The Phoenix service, its database, login, retention, and the public HTTPS
  site.

### Out of scope

- Phoenix evaluations, datasets and prompt management.
- Tracing work done in the frontend.
- Alerting or metrics dashboards.

## 4. Architecture

```text
9XAIPal API / Celery worker                       Phoenix (new container)
  app/core/tracing.py  ── OTLP/HTTP (batched) ──►  9xaipal-phoenix :6006 (internal only)
    spans: ask → route → retrieve → llm ...          store: database "phoenix" on 9xaipal-postgres
           ingest → extract → chunk → embed ...      auth on, retention 14 days
                                                           ▲
Browser ── https://9xaipal-trace.kl1.site ── host nginx ───┘ (TLS by certbot)
```

### 4.1 Recorder: `backend/app/core/tracing.py`

The only module that imports OpenTelemetry. Everything else uses its API:

- `span(name, kind, **attributes)` — context manager and decorator (sync and
  async). Kinds follow OpenInference: `CHAIN`, `LLM`, `RETRIEVER`, `TOOL`,
  `EMBEDDING`.
- `record_input(value)`, `record_output(value)`, `set_attributes(**values)` on
  the current span.
- `inject_context()` / `extract_context(carrier)` for passing the trace across
  Celery tasks.
- Initialization is lazy and happens once per process (API and each worker
  process). It configures a `BatchSpanProcessor` with an OTLP/HTTP exporter to
  `PHOENIX_COLLECTOR_ENDPOINT`, authenticated with `PHOENIX_API_KEY`.

When `TRACE_ENABLED` is false, or initialization fails, every function is a
no-op that returns immediately.

### 4.2 Instrumentation points

Wrappers only; no computation, argument, return value or exception changes.

| Area | Location | Block |
| --- | --- | --- |
| Chat entry | `chat/orchestrator.py` | `ask` (root) and one block per existing `ASK[step…]` |
| Routing | `chat/router.py` | `route` — decision, reason, confidence |
| Context | `chat/local_context.py`, `global_context.py`, `overview_context.py`, `external_context.py` | `build_context` (RETRIEVER) |
| Retrieval | `services/retrieval.py`, embeddings service | `retrieve`, `embed_query` (EMBEDDING) |
| Web search | `search/web.py` | `web_search` (TOOL) — each provider tried, skipped, failed, or used |
| Agents | `chat/paper_agent.py`, `research_agent.py`, `study_agent.py`, `agent_tools.py` | `agent_step`, `tool:<name>` |
| LLM | `llm/client.py` (`chat`, `stream_chat`, `chat_sync`) | `llm.chat` (LLM) |
| Ingestion tasks | `workers/tasks.py` | one block per task, joined into one `ingest` trace |
| PDF pipeline | `extraction/pipeline_sync.py` | `extract`, `classify`, `chunk`, `glyph_repair`, `heading_repair`, `code_crops`, `assets`, `persist` |
| Article pipeline | `extraction/pipeline_sync.py` (`run_article_pipeline_sync`), `services/article_extraction.py` | `fetch`, `extract_article`, `assets` |
| Embeddings | `embeddings/service_sync.py` | `embed_batch` (EMBEDDING) |
| Summaries | `summarization/*` | `summarize_section`, `describe_figure` and their `llm.chat` children |

## 5. Trace shapes

```text
ask  [CHAIN]  user, conversation_id, doc_ids, question → final answer, latency, tokens
├─ route              [CHAIN]     → Local | Global | External + reason, confidence
├─ build_context      [RETRIEVER] query → chunks (id, page, score, text ≤ 2 KB each)
│   └─ embed_query    [EMBEDDING] model, dimensions
├─ web_search         [TOOL]      query → providers tried/skipped/failed, results (title, url, snippet)
├─ agent_step N       [CHAIN]     tool chosen, arguments
│   └─ tool:<name>    [TOOL]      input → output
├─ llm.chat           [LLM]       model, provider, messages, answer, prompt/completion tokens, time to first token
└─ persist            [CHAIN]     turn ids, citations

ingest  [CHAIN]  document_id, filename, kind (paper/book/article/url), user → final status
├─ queue_wait               queued → worker pickup
├─ process_ingestion        (task)
│   ├─ classify             (when Arabic OCR is enabled)
│   ├─ extract              extractor, pages, progress events, output files
│   │   └─ llm.chat …       (VLM / Gemini / Gemma page calls)
│   ├─ chunk                source (content_list | markdown), chunk counts by type, first chunks
│   ├─ glyph_repair / heading_repair   counts changed
│   ├─ assets               images referenced / found / moved / missing
│   └─ persist              chunks and assets written
├─ embed_document           (task) chunks embedded, model, batches
│   └─ embed_batch          [EMBEDDING]
└─ generate_section_summaries (task) sections
    └─ llm.chat …
```

### 5.1 Common attributes

Every trace carries `user.id`, and where they apply `document.id`, `job.id`
and `session.id` (the conversation id), so Phoenix can filter by user,
document, conversation, error or latency. Every block carries `status`
(`ok`, `error`, or `cancelled`) and, on error, the exception type and message.

### 5.2 Size caps and redaction

- Text values are capped at `TRACE_MAX_TEXT_CHARS` (default 8,000) and marked
  as truncated; retrieved chunk text is capped at 2,000 characters per chunk.
- Arrays keep the first 20 items plus a total count.
- Image bytes are never recorded, only names, types and sizes.
- Keys named `authorization`, `api_key`, `*_api_key`, `password`, `secret`,
  `token`, and `cookie` (case-insensitive) are replaced with `[redacted]`
  before any value is recorded.

### 5.3 Celery linking

The API injects the current trace context into the task headers when it
dispatches `process_ingestion` or `process_article_ingestion`; each task
extracts it and starts its block as a child. Tasks that dispatch the next
task (`embed_document` → `generate_section_summaries`, figure descriptions)
pass the context the same way, so one upload is one trace. `queue_wait` is
computed from the job's `created_at` to task start.

## 6. Phoenix service

- `docker-compose.prod.yml` gains `9xaipal-phoenix` (image pinned to a
  specific Phoenix release), on the internal network only, with no published
  port.
- Storage: database `phoenix` on the existing `9xaipal-postgres`, created
  idempotently by the deploy step. Phoenix manages its own schema.
- Limits: 512 MB memory, 1 CPU.
- Environment: `PHOENIX_ENABLE_AUTH=true`, `PHOENIX_SECRET`,
  `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD`,
  `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=14`, `PHOENIX_SQL_DATABASE_URL`.
- The development `docker-compose.yml` gains the same service with a local
  port for testing.

## 7. Security and access

- The trace site is reachable only through host nginx over HTTPS.
  `backend/nginx/9xaipal-trace.conf` is added to the repository; the owner
  installs it and obtains the certificate with `sudo`.
- Phoenix login is required. The owner changes the initial admin password on
  first login.
- The app authenticates to Phoenix with a system API key, never a user
  account.
- Secrets live only in the server's `backend/.env`: `PHOENIX_SECRET`
  (32 or more random characters), `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD`,
  `PHOENIX_API_KEY`. None are committed, logged, or recorded in traces.

## 8. Rollout

1. Merge and deploy. Phoenix starts, and its database is created.
   `TRACE_ENABLED` defaults to `false`, so the app behaves exactly as before.
2. The owner adds the DNS A record, installs the nginx server block, and runs
   `certbot --nginx -d 9xaipal-trace.kl1.site`.
3. Log in to Phoenix, create the system API key, add it to `.env`, set
   `TRACE_ENABLED=true`, and restart only the API and worker.
4. Verify that one upload and one chat question each produce a complete trace.

Kill switch: set `TRACE_ENABLED=false` and restart the API and worker.

## 9. Error handling and testing

### 9.1 Tracing never breaks the app

- Every recorder call is wrapped. Exporter, serialization and attribute errors
  are logged at most once per minute and dropped.
- With Phoenix down, spans queue in a bounded buffer (2,048) and the oldest are
  dropped. Requests and tasks never wait for an export; on worker shutdown a
  flush of at most 2 seconds runs.
- A streaming answer's `llm.chat` block closes when the stream ends or the
  client disconnects; a disconnect is recorded as `cancelled` with the partial
  answer.
- An exception inside a traced block is recorded and then re-raised unchanged.

### 9.2 Tests (test-first)

1. **Recorder:** no-op when disabled or when Phoenix is unreachable;
   truncation; redaction (no key or `Authorization` value ever recorded);
   exceptions propagate unchanged and mark the block as an error; a failing
   recorder never fails the caller.
2. **Span trees** with an in-memory OpenTelemetry exporter: `/ask` for each
   route produces the expected blocks and attributes; ingestion produces
   `ingest` → `process_ingestion` → extraction, chunking, repairs, assets,
   persistence; `embed_document` and `generate_section_summaries` join the
   same trace; LLM blocks carry model, tokens, input and output.
3. **English isolation proof:** the main-vs-branch differential harness on 15
   recorded MinerU documents, run with tracing off and with tracing on
   (exporting to an in-memory collector). Persisted chunks, assets, document
   and job rows, stored files and dispatched tasks must be identical to
   `main`. Plus the full backend and frontend suites.
4. **Live check after deploy:** one upload and one chat question, with their
   traces verified in the browser at `https://9xaipal-trace.kl1.site`.

## 10. Configuration

```dotenv
TRACE_ENABLED=false
TRACE_MAX_TEXT_CHARS=8000
PHOENIX_COLLECTOR_ENDPOINT=http://9xaipal-phoenix:6006/v1/traces
PHOENIX_API_KEY=
PHOENIX_SECRET=
PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=
PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=14
```

`TRACE_*` and `PHOENIX_COLLECTOR_ENDPOINT` / `PHOENIX_API_KEY` go to the API
and worker; the remaining `PHOENIX_*` values go to the Phoenix service only.
