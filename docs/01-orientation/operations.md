# Operations — logging, failure modes, recovery

> **What this is:** the runbook. What to look at when something is wrong, what each failure looks
> like from the outside, and the SQL to inspect live state.
>
> **How to read it:** §1 where the signals are → §2 failure catalog → §3 recovery recipes →
> §4 performance baselines.
>
> **Owns:** diagnosis and recovery procedures.
> **Does not own:** install ([setup.md](setup.md)), service inventory
> ([runtime-topology.md](runtime-topology.md)).
>
> **Status:** current · **Last verified:** 2026-07-25

---

## 1. Where the signals are

| Signal | Where | Use it for |
| --- | --- | --- |
| `ASK[stepN]` log lines | API stdout | Tracing one question through routing → retrieval → LLM |
| `[celery]` / `[pipeline]` prefixes | Worker stdout | Ingestion progress and failures |
| `ask_traces` table | Postgres | Latency, token counts, which route/model served a question |
| `conversation_turns` table | Postgres | Full chat log incl. `router_reason` and `citations` |
| `ingestion_jobs` table | Postgres | Pipeline state machine, `started_at` / `completed_at` |
| `GET /api/v1/health` | HTTP | Liveness of database / Ollama / SearXNG |
| `docker compose ps` | Shell | Container health status (`unhealthy` triggers autoheal) |

### Ops: inspect ask traces

```sql
SELECT created_at, context_type, model, latency_ms,
       prompt_tokens, completion_tokens
FROM ask_traces
ORDER BY created_at DESC
LIMIT 50;
```

### Ops: inspect pipeline state

```sql
SELECT j.status, j.started_at, j.completed_at,
       d.status AS doc_status, d.extractor, d.error_message
FROM ingestion_jobs j
JOIN documents d ON d.id = j.document_id
ORDER BY j.created_at DESC
LIMIT 20;
```

### Ops: inspect conversations for a paper

```sql
SELECT conversation_id, COUNT(*) AS turns,
       MIN(created_at) AS started, MAX(created_at) AS last
FROM conversation_turns
WHERE document_id = '<uuid>'
GROUP BY conversation_id
ORDER BY last DESC;
```

---

## 2. Failure catalog

| Failure | Detected by | Behavior | User-visible | Recovery |
| --- | --- | --- | --- | --- |
| Postgres unreachable | `/health` → `database:"unavailable"` | Requests 5xx; lifespan does **not** crash | Errors on every action | Start Postgres — the API recovers on the next request. In compose, `restart:` + autoheal do it |
| No AI backend at all | 503 `NO_LLM_CONFIGURED` | Chat fails; papers still serve | *"No AI backend is configured. Put your API key or your Ollama connection in backend/.env…"* | Start Ollama or paste one cloud key |
| Ollama down, cloud key present | resolver probe | `auto` reroutes to the first cloud key | Nothing — after up to 30 s (probe cache) | None; restart Ollama to shift back |
| Ollama model not pulled | First call hangs | Model downloads mid-request | Very slow first answer | `ollama pull <CHAT_MODEL>` ahead of time |
| Embedding model switched | Startup comparison | Pinned ⇒ wipe + re-embed. `auto` ⇒ warn only | Long startup, or degraded search | Pin `EMBEDDING_PROVIDER` and restart |
| Redis down | Upload marks doc `failed` | Descriptive `error_message` | *"Start Redis (e.g. via docker compose…)"* | Start Redis + worker |
| No Celery worker running | Nothing — no error at all | Job sits in Redis unconsumed | ⚠ Overlay spins at `queued` forever | Start the worker |
| Worker OOM on a large book | exit 137, container restarts | Uploads pause, then resume from Redis | Brief stall | Lower `MINERU_PAGE_BATCH_SIZE` or raise `WORKER_MEM_LIMIT` |
| Container hung but running | Healthcheck `unhealthy` | autoheal restarts it | Brief stall | Automatic |
| Worker crashes mid-ingestion | Doc stuck in `extracting`/`chunking`/`embedding` | Job never completes | Overlay spins | `POST /papers/{id}/reextract` |
| MinerU not installed | `MinerUError` | Doc marked `failed` | Error in the overlay | Install MinerU, or `ALLOW_PYMUPDF_FALLBACK=true` for degraded mode |
| Web search down or misconfigured | EXTERNAL and the paper agent's `WEB` tool return `[]` | Answer is ungrounded but does not crash | Fewer/no web citations; a trail row reading "nothing came back" | On `searxng`: `docker compose up -d searxng`. On `tavily`: check the logs for `Tavily search failed: HTTP 401` (bad key) or `429` (quota) — `/health` cannot tell you, see [configuration.md § Web search](../03-reference/configuration.md#web-search) |
| Answers wander off-domain | — | Guardrail + `DOMAIN_PREAMBLE` + cross-field gate | Off-topic content | See [chat-and-ask.md](../02-architecture/chat-and-ask.md) |
| Off-domain citation chips | — | `_filter_unused_web_citations` drops URLs absent from the answer | Stray chips | Already mitigated |
| `Sources: None.` in the answer | — | Stripped server-side; defensive client strip in `ChatPane` | A stray literal line | Already mitigated |

---

## 3. Recovery recipes

### Reset one paper completely

```sql
DELETE FROM documents WHERE id = '<uuid>';
-- cascades: chunks, chunk_embeddings, chunk_assets, section_summaries,
--           figure_descriptions, ingestion_jobs, paper_notes
-- conversation_turns survive with document_id = NULL
```

Pair with disk cleanup if `DELETE /papers/{id}` was bypassed:

```bash
rm -rf backend/app/storage/{documents,assets,extracted,images}/*<id>*
```

### Force a re-embed of one paper

```sql
DELETE FROM chunk_embeddings
WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = '<uuid>');
```

Then `POST /papers/{id}/rechunk`, which re-queues `embed_document`.

⚠ Under `INGEST_PROFILE=fast` a paper has no embeddings to re-queue and `/rechunk` dispatches
nothing — it is answered live by the paper agent instead. The response says which happened.

### Repair figures or mangled inline math on an existing paper

`POST /papers/{id}/rechunk` also, in one pass:

- re-links every figure to its image (see the landmine below),
- attaches MinerU's cropped bitmap to each `math` chunk,
- runs the U+FFFD glyph repair, reporting `glyphs_repaired` in the response.

It reuses the cached MinerU output, so it costs seconds rather than a re-extraction. Notes survive
— they anchor on `sequence_id`, not on chunk ids.

⚠ **Any browser tab opened before a re-chunk holds stale block ids.** The notes endpoint re-resolves
the anchor from `sequence_id` for exactly this reason, but reload the reader anyway to see the new
chunking.

### Repair ladder — cheapest first

| Endpoint | Cost | Does |
| --- | --- | --- |
| `POST /papers/{id}/rechunk` | Seconds | Re-runs the chunker on cached MinerU output. No re-extraction |
| `POST /papers/{id}/reconstruct-reading-order` | ~1 LLM call | Fixes two-column reading order |
| `POST /papers/{id}/regenerate-summaries` | Minutes | Re-runs summaries + VLM figure descriptions |
| `POST /papers/{id}/reextract` | Full pipeline | Wipes cached extraction and re-runs MinerU |

Always try `rechunk` before `reextract` — MinerU is by far the expensive half.

### Scale workers

```bash
celery -A app.core.celery_app worker --loglevel=info --concurrency=4 -n w1@%h
celery -A app.core.celery_app worker --loglevel=info --concurrency=4 -n w2@%h
```

---

## 4. Performance baselines

Target machine: Apple M4 Max, 48 GB unified, local models. Measured 2026-07; treat as orders of
magnitude, not guarantees.

⚠ **These are a floor, not this hardware's ceiling.** Ingestion figures were measured with the
Celery worker running in Docker, and **Docker on macOS cannot access MPS or MLX** — so extraction
ran CPU-only. MinerU's `vlm-engine` routes to MLX on Apple Silicon when run on the host. See
[plans/pdf-parser-evaluation.md §7](../plans/pdf-parser-evaluation.md).

| Operation | Expected |
| --- | --- |
| Upload + ingestion (10-page paper, MinerU) | 1–2 min |
| Embedding (~80 chunks) | 30–60 s |
| Section summarization (10-page paper) | 5–15 min |
| `/ask` LOCAL (1 image) | 8–30 s |
| `/ask` GLOBAL (top-3 chunks) | 5–20 s |
| `/ask` OVERVIEW | 5–15 s |
| `/ask` EXTERNAL + research agent (3 iterations) | 30–120 s |

Levers, in rough order of effect: set `CLASSIFIER_MODEL` to a small model; keep
`OLLAMA_KEEP_ALIVE` long enough that the big model stays resident; lower `LOCAL_CONTEXT_WINDOW`;
raise Celery `--concurrency`.
