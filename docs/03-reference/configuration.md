# Configuration reference

> **What this is:** the canonical table of every environment variable the backend reads. This is
> the single home for that fact — other docs link here rather than restating defaults.
>
> **Owns:** env-var names, defaults, and what each one does.
> **Does not own:** how the provider chain resolves ([ai-backend.md](../02-architecture/ai-backend.md)),
> how to bring the stack up ([setup.md](../01-orientation/setup.md)).
>
> **Status:** current · **Last verified:** 2026-07-25 against
> [`app/core/config.py`](../../backend/app/core/config.py) (`main`, 9b75500)
> **Verify with:** `python -c "from app.core.config import settings; print(settings.model_dump())"`
> **Volatile:** the whole file mirrors `config.py` — re-verify on any change to that file.

Loaded by pydantic-settings from `backend/.env`, falling back to the process environment.
`extra="ignore"`, so an unknown key never breaks startup — and never warns either. ⚠ A typo'd key
is silently ignored; if a setting seems not to apply, check the spelling first.

---

## Application

| Key | Default | Purpose |
| --- | --- | --- |
| `DEBUG` | `false` | Verbose logging. |
| `STORAGE_ROOT` | `app/storage` | Root of all on-disk artifacts. Relative paths resolve from the process CWD. |

## PostgreSQL

| Key | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_HOST` | `localhost` | |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `9xaipal` | |
| `POSTGRES_USER` | `9xaipal` | |
| `POSTGRES_PASSWORD` | `9xaipal_dev_password` | ⚠ Startup warns while this default is in place. Rotate before exposing the app to anything. |
| `DB_POOL_SIZE` | `10` | SQLAlchemy pool. Raise for many concurrent `/ask`. |
| `DB_MAX_OVERFLOW` | `15` | |

Derived, not settable: `database_url` (asyncpg, used by the API) and `database_url_sync`
(psycopg2, used by Celery workers).

## LLM provider

Full resolution logic: [ai-backend.md](../02-architecture/ai-backend.md).

| Key | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `auto` | `auto` = Ollama if reachable, else first cloud key in order openai → anthropic → gemini → xai → deepseek. Pin with `ollama` \| `openai` \| `anthropic` \| `gemini` \| `xai` \| `deepseek` \| `custom`. |
| `LLM_API_KEY` | (empty) | Generic key for a pinned provider. Per-provider keys win when both are set. |
| `LLM_BASE_URL` | (provider default) | Required for `custom`; otherwise an override (Azure, OpenRouter, vLLM). |
| `OPENAI_API_KEY` | (empty) | |
| `ANTHROPIC_API_KEY` | (empty) | |
| `GEMINI_API_KEY` | (empty) | |
| `XAI_API_KEY` | (empty) | |
| `DEEPSEEK_API_KEY` | (empty) | |
| `OPENAI_CHAT_MODEL` | `gpt-4o` | |
| `ANTHROPIC_CHAT_MODEL` | `claude-sonnet-4-6` | |
| `GEMINI_CHAT_MODEL` | `gemini-2.5-flash` | |
| `XAI_CHAT_MODEL` | `grok-4` | |
| `DEEPSEEK_CHAT_MODEL` | `deepseek-chat` | ⚠ No vision — figure images cannot be described on this provider. |
| `CLOUD_THINKING_MODE` | `false` | Sends `reasoning_effort: "medium"` to OpenAI-compatible reasoning models. Ignored elsewhere. |

## Ollama

⚠ These four keys are **reserved for Ollama** (and `custom`). An Ollama tag is never sent to a
cloud API — each cloud provider has its own `*_CHAT_MODEL` above.

| Key | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | ⚠ Compose services override this to `http://host.docker.internal:11434` — inside a container, `localhost` is the container. |
| `CHAT_MODEL` | `gemma4:26b` | Answer model. |
| `VLM_MODEL` | (empty → reuses `CHAT_MODEL`) | Vision model for figure descriptions and image questions. |
| `CLASSIFIER_MODEL` | (empty → reuses `CHAT_MODEL`) | Router + guardrail only. Pointing this at a small fast model is the single biggest `/ask` speedup. |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long a model stays resident. `-1` = forever, `0` = unload immediately. |
| `OLLAMA_FLASH_ATTENTION` | `0` | Set on the **Ollama process**, not the app. Required for stable quantized gemma4. |

## Embeddings

| Key | Default | Purpose |
| --- | --- | --- |
| `EMBEDDING_PROVIDER` | `auto` | `auto` = Ollama if reachable, else OpenAI, else Gemini (the only clouds with embedding APIs). ⚠ Pinning arms the auto-wipe: a pinned provider whose model differs from what's stored **deletes all vectors and re-embeds the library** on next start. |
| `EMBEDDING_MODEL` | `qwen3-embedding` | Ollama embedding model. ⚠ Must be a tag that actually exists locally — a bare name with no matching tag 404s at first embed. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | |
| `EMBEDDING_API_KEY` | (falls back to `LLM_API_KEY`) | Override only. |
| `EMBEDDING_BASE_URL` | (falls back) | Override only. |
| `EMBED_MAX_CHARS` | `3000` | Chars per chunk sent to the embedder. Ollama 400s on over-window input; dense tables tokenize heavily. Raise for cloud embedders. |
| `VECTOR_DIMENSION` | `1024` | Stored vector size. Larger model outputs are truncated + renormalized (valid for MRL-trained models); smaller are zero-padded. ⚠ Keep ≤ 2000 — pgvector's HNSW index has a hard 2000-dim limit, and without the index every search is a full scan. ⚠ Changing this wipes and re-embeds the library. |

## Extraction

| Key | Default | Purpose |
| --- | --- | --- |
| `MINERU_BINARY` | `mineru` | Absolute path if not on `$PATH`. MinerU 3.x only — `magic-pdf` 0.x is unsupported. |
| `MINERU_LANG` | `en` | OCR language hint. |
| `MINERU_TIMEOUT_SEC` | `14400` (4 h) | Wall clock for one MinerU subprocess. High by default because a 700-page book on CPU takes hours. |
| `ALLOW_PYMUPDF_FALLBACK` | `false` | `true` degrades to text-only extraction when MinerU is missing — no OCR, no tables, no math. Default `false` so a missing extractor fails loudly. |
| `MINERU_PAGE_BATCH_SIZE` | `100` | Compose-only. Extract in page-range batches so peak RAM stays bounded on long books. `0` disables. |
| `MAX_UPLOAD_SIZE_MB` | `100` | Hard cap on the upload body. |

## Ingest profile

Which pipeline a document takes after chunking. Detail:
[ingestion-pipeline.md](../02-architecture/ingestion-pipeline.md).

| Key | Default | Purpose |
| --- | --- | --- |
| `INGEST_PROFILE` | `fast` | `fast` = a paper is complete once MinerU and the chunker have run: no embeddings, no section summaries, no VLM figure descriptions. `full` = the historical chain, still honouring `PAPER_ONLY_MODE` below. |
| `VLM_MAX_CONCURRENCY` | `4` | Figure descriptions generated in parallel (`full` profile only). The calls are network I/O, so a small pool turns wall-clock from the sum of every figure into roughly the slowest one. `1` restores sequential. ⚠ Concurrent load on one inference endpoint — a hosted one may rate-limit. |

⚠ The profile applies **only to `doc_kind='paper'`**. A book always takes the full chain: it can
neither be stuffed into a context window nor usefully full-text scanned, so it still needs vectors
to be answerable.

⚠ Under `fast`, `documents.embedding_mode` is recorded as `'skipped'` with reason `fast_ingest`,
and the document reaches `status='complete'` inside `run_pipeline_sync` rather than at the end of
the Celery chain. That is the one other place completion is set, and it is safe precisely because
the fast path dispatches nothing afterwards
([`pipeline_sync.py`](../../backend/app/extraction/pipeline_sync.py)).

## Paper agent

How a question is answered when there are no embeddings. Detail:
[chat-and-ask.md](../02-architecture/chat-and-ask.md).

| Key | Default | Purpose |
| --- | --- | --- |
| `WHOLE_PAPER_MAX_TOKENS` | `120000` | Stuff the entire document into the prompt when `SUM(chunks.token_count)` is at or below this. Above it, the agent falls back to an iterative `SEARCH`/`READ` loop. ⚠ `token_count` is a `len/4` heuristic that undercounts math and tables — leave headroom below the model's real window. |
| `PAPER_AGENT_MAX_STEPS` | `4` | Tool rounds before the model is forced to answer with what it has. |
| `PAPER_AGENT_READ_MAX_CHUNKS` | `40` | Ceiling on one `READ` range, so a greedy request cannot blow the context window. Enforced in SQL. |
| `PAPER_AGENT_SEARCH_LIMIT` | `8` | Ceiling on hits returned by one `SEARCH`. |

## Paper-only mode

⚠ `[historical]` for papers — superseded by `INGEST_PROFILE=fast`, which skips more than just
embeddings. These keys still govern the `full` profile and books.

Skip the embedding pass for documents that fit whole in the chat model's context. Design and
rationale: [paper-only-embedding-skip.md](../plans/paper-only-embedding-skip.md).

| Key | Default | Purpose |
| --- | --- | --- |
| `PAPER_ONLY_MODE` | `false` | Master switch. Default off so upgrading changes nothing. ⚠ Only segment S1 has landed — with this on, a skipped document's GLOBAL route degrades to full-text search alone until S2 adds the stuffing fallback. |
| `PAPER_ONLY_MAX_TOKENS` | `50000` | Skip only when `SUM(chunks.token_count)` is at or below this. ⚠ Not set near the model's full window: the body shares it with the system prompt, history, and the answer — and `token_count` is a `len/4` heuristic that undercounts math and tables. |

The decision is made **once, at ingestion**, and stored in `documents.embedding_mode`
(`'embedded'` | `'skipped'`) with `embedding_skip_reason`. It is never re-derived, so changing
either setting cannot retroactively reclassify a library that is already ingested.

⚠ `doc_kind` is a **guard, not the gate**: a book is never skipped, but `doc_kind` defaults to
`'paper'` for every document predating the book/paper chooser, so gating on it alone would sweep
in the entire existing library. The gate is measured token count.

## Chat behavior

| Key | Default | Purpose |
| --- | --- | --- |
| `LOCAL_CONTEXT_WINDOW` | `3` | Chunks on each side of the anchor. Used by the LOCAL route **and** by the paper agent for the window around a note's anchor. |
| `GUARDRAIL_SKIP_IN_PAPER` | `true` | Skip the topic guardrail while reading a paper — paper Q&A is in-scope by definition, so this removes a model call per question. |
| `CHAT_NUM_PREDICT` | `0` | Cap on answer length. `0` = uncapped. |
| `MAX_CONCURRENT_ASKS` | `3` | Concurrent `/ask` per uvicorn worker; protects Ollama from OOM. |

## Web search

Being replaced — see [plans/exa-firecrawl-research-stack.md](../plans/exa-firecrawl-research-stack.md).

| Key | Default | Purpose |
| --- | --- | --- |
| `SEARXNG_URL` | `http://localhost:8080` | Point at an unreachable URL to disable the EXTERNAL path entirely. |

## Background jobs

| Key | Default | Purpose |
| --- | --- | --- |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker + result backend. |
| `CELERY_BROKER_URL` | (falls back to `REDIS_URL`) | Override only. |
| `CELERY_RESULT_BACKEND` | (falls back to `REDIS_URL`) | Override only. |

## Security

| Key | Default | Purpose |
| --- | --- | --- |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173` | Comma-separated. Add your LAN address when serving the dev UI to other machines. Irrelevant in single-port SPA mode (same origin). |
| `RATE_LIMIT_PER_MINUTE` | `300` | Per-client-IP ceiling across `/api`. `0` disables. ⚠ In-memory and **per-process** — with `--workers 2` the real ceiling is 600. |
| `SERVE_FRONTEND` | `true` (compose) | Serve the built SPA at `/` from the API container. |

## MinerU weights

| Key | Default | Purpose |
| --- | --- | --- |
| `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` | (empty) | Read by `huggingface_hub` for MinerU's ~5 GB first-run weight download. |
