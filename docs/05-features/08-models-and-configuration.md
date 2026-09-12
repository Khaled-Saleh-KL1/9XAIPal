# Area 8 — Models & configuration (features 95–100)

> Part of the [feature catalogue](README.md). Companions:
> [ai-backend.md](../02-architecture/ai-backend.md), [configuration.md](../03-reference/configuration.md).
>
> **Reflects code as of:** 2026-09-12 (`main`, c099d90).

The rule that shapes this whole area: **no call site names a model.** Callers say what *kind* of
call they make — a `role` of `chat`, `classifier`, `vlm`, `embedding` — and the resolver maps the
role to a model for whichever provider is active. The one exception is the reader's own per-note
choice (feature 97), which is a request-time value, not a hardcoded one.

---

## 95. Ollama local models and five cloud providers

**What it does.** Chat can be served by a local Ollama (the live box: `gemma4:31b`), by Ollama's
cloud tags, or by OpenAI, Anthropic, xAI, DeepSeek, or any OpenAI-compatible endpoint (`custom`:
OpenRouter, vLLM, llama.cpp server). Switching to a cloud provider is **pasting one API key and
nothing else**.

**Where.** [`llm/client.py`](../../backend/app/llm/client.py) (`chat`, `stream_chat`, `chat_sync`),
[`llm/resolver.py`](../../backend/app/llm/resolver.py) (`llm_cascade`),
[`llm/ollama_client.py`](../../backend/app/llm/ollama_client.py), `LLM_PROVIDER`
(`auto` | `ollama` | `openai` | `anthropic` | `xai` | `deepseek` | `custom`).

**How it works.** Two model namespaces: the Ollama one (`CHAT_MODEL`, `VLM_MODEL`,
`CLASSIFIER_MODEL`, `EMBEDDING_MODEL`) and the cloud one (`OPENAI_CHAT_MODEL`,
`ANTHROPIC_CHAT_MODEL`, …). Because they are separate, your Ollama tags stay where they are and
are simply not used when a cloud key is active. Under `auto`, every call is a **cascade**: probe
Ollama's `/api/tags` (3 s timeout, cached 30 s); if reachable the cascade is `[ollama, …every
cloud key present]`, else the cloud keys in order; each target is tried with the *same* messages
until one succeeds. A pinned provider means exactly that backend, no fallback. Local Ollama uses
its native API (tag resolution, `keep_alive`); every cloud provider speaks the OpenAI
chat-completions protocol, so one HTTP implementation covers all of them.

**Why the cascade.** Ollama passing its cheap reachability probe never guaranteed the completion
would succeed — the wrong tag, an OOM, a crash mid-generation used to be fatal with a cloud key
sitting right there in `.env`. ⚠ Streaming's fallback covers only pre-first-token failures: once a
token has reached the reader, silently restarting on another provider would be worse than a clean
error. ⚠ Ollama cloud tags (`gemma4:31b-cloud`) are served through `localhost:11434` but proxied to
`ollama.com`, so traces cannot distinguish a cloud-served answer from a local one.

---

## 96. Model roles

| Role | Ollama setting | Called from | Frequency |
| --- | --- | --- | --- |
| `chat` | `CHAT_MODEL` | orchestrator, paper agent, study agent, judge, summariser, reading order | 1× per question (+1 per tool round) |
| `vlm` | `VLM_MODEL` → `CHAT_MODEL` | figure describer, page-image questions | 1× **per figure** at ingestion |
| `classifier` | `CLASSIFIER_MODEL` → `CHAT_MODEL` | router, guardrail | ≤ 2× per `/ask` |
| `embedding` | `EMBEDDING_MODEL` | ingestion, GLOBAL, library search, memory | 1× per chunk, then per query |

**Why the split.** The classifier row is the performance lever: router and guardrail are cheap
classification problems a 1–3 B model answers instantly; left empty they inherit `CHAT_MODEL` and
put two full-size calls in front of every question. The orchestrator also runs guardrail and
router concurrently (`asyncio.gather`), and `GUARDRAIL_SKIP_IN_PAPER=true` short-circuits the
guardrail while reading — an in-paper question costs one classifier call, not two. The `vlm` row is
the cost lever: a figure-heavy paper is dozens of vision calls before anyone asks anything, which
is why `GENERATE_FIGURE_DESCRIPTIONS` is off on the live box. DeepSeek has no vision; with it
active figures cannot be described.

---

## 97. `/models` list and per-request override

**What it does.** The composer's model picker lists what is available; the choice rides on the
note (and its follow-ups), or on a desk question.

**Where.** [`llm/catalog.py`](../../backend/app/llm/catalog.py), `GET /models`,
`paper_notes.requested_model`, `llm_client.chat(model=…)`.

**How it works.** The catalog reads Ollama's `/api/tags` and splits **local** (real weights on
disk, non-zero `size`) from **cloud** (name ending in `cloud`, `size: 0`); embedding models are
filtered out (they share the tag list but cannot chat). When Ollama is unreachable the catalog
degrades to the single configured cloud model. The requested model is persisted on the note so
the answer stays attributable; a follow-up reuses its parent's model and cannot be overridden.

**Why it does not break the no-hardcoding rule.** The name comes from the user at request time —
nothing in the code names it.

---

## 98. Multi-key rotation

**What it does.** `TAVILY_API_KEY` and `OLLAMA_API_KEY` accept a **comma-separated list**; an
exhausted or rejected key falls through to the next *carrying the same request*, and only once
every key is spent does the caller see a failure.

**Where.** [`search/tavily_client.py`](../../backend/app/search/tavily_client.py),
`llm/ollama_client.py`, `tests/test_multi_key_rotation.py`.

**Why.** Several providers give a free allowance *per key*, so more headroom is more keys, not a
paid plan — the standing rule that no provider in the cascade may cost money.

---

## 99. The embedding pin

**What it does.** Guarantees every vector in the library was produced by the same model, because
vectors from different models are not comparable and mixing them silently destroys retrieval —
no error, just worse answers.

**Where.** `embeddings/model.py` (`active_embedding_model`), `core/lifecycle.py` (startup check),
`chunk_embeddings.embedding_model`, `VECTOR_DIMENSION` (1024).

**How it works.** Three mechanisms. (1) *Per-process pinning*: after the first successful
resolution the choice is fixed for that process — a transient Ollama hiccup mid-ingestion cannot
switch models halfway. (2) *Startup mismatch detection*: the lifespan compares the stored
`embedding_model` with the configured one and refuses to mix. (3) *Dimension normalisation*:
larger outputs are truncated and re-normalised (valid for MRL-trained models like
`qwen3-embedding` and `text-embedding-3-*`), smaller ones zero-padded; keep it ≤ 2000, pgvector's
HNSW hard limit, or every search degrades to a brute-force scan. ⚠ Changing `VECTOR_DIMENSION`, or
`EMBEDDING_MODEL` with a pinned provider, is a destructive operation with no confirmation prompt;
the safe path is the re-embed pass (feature 32).

---

## 100. Health endpoint and the circuit breaker

**What it does.** `GET /health` reports `database`, `ollama` (the LLM cascade), `web_search` with
the provider currently first in line, and which providers are `tripped`. The deploy's health check
and the autoheal container both read it.

**Where.** [`endpoints/health.py`](../../backend/app/api/v1/endpoints/health.py),
[`core/circuit_breaker.py`](../../backend/app/core/circuit_breaker.py) (`FAILURE_THRESHOLD` 3,
`COOLDOWN_SECONDS` 300).

**How it works.** Both cascades (web search, chat) try providers in fixed priority order; after
three consecutive failures a provider is skipped for five minutes, then given one trial call;
success resets it. Its *priority* never changes — a recovered provider is used again automatically.
Semantic Scholar has a breaker too, tripped only by a final failure after the paced retries.

**Why.** The concrete case: a valid Gemini key whose search-grounding quota was zero from an
EEA-hosted server — first in the cascade, failing 100 % of calls, costing ~0.13 s of every search
plus an ERROR line, thousands of times.
