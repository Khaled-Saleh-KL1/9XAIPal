# Area 8 — Models & configuration (features 95–100)

> Part of the [feature catalogue](README.md). Companions:
> [ai-backend.md](../02-architecture/ai-backend.md), [configuration.md](../03-reference/configuration.md).
>
> **Reflects code as of:** 2026-10-06 (`fix/model-picker`).

The rule that shapes this whole area: **no call site names a model.** Callers say what *kind* of
call they make — a `role` of `chat`, `classifier`, `vlm`, `embedding` — and the resolver maps the
role to a model for whichever provider is active. The reader's request-time choice for a note or
Desk study question (feature 97) overrides that role mapping for the selected answer.

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
`ANTHROPIC_CHAT_MODEL`, …). Calls with no picker override use the default provider route. Under
`auto`, it probes Ollama's `/api/tags` (3 s timeout, cached 30 s); if reachable the default cascade
is `[ollama, …every cloud key present]`, else it walks the configured cloud keys in order. Each
default target uses its own configured role model. A configured `LLM_PROVIDER` pin uses only that
provider. A request-time model choice is routed to its owner: names in
`resolver.MODEL_PROVIDER_PINS` use their pinned provider, provider-specific configured defaults
use that provider, and other catalog tags use Ollama alone. This prevents a model tag from being
sent to an unrelated provider. Local Ollama uses its native API (tag resolution, `keep_alive`);
every cloud provider speaks the OpenAI chat-completions protocol.

**Why the cascade.** Ollama passing its cheap reachability probe never guaranteed the completion
would succeed — the wrong tag, an OOM, or a crash mid-generation could fail a request. For a
streamed picker request, an owner-provider failure before answer output can retry the configured
default route, with a visible notice before fallback answer tokens. The terminal answer metadata
identifies the model that actually answered. Once answer text has reached the reader, the stream
does not restart on another model. Ollama cloud tags can be served through a local daemon and
proxied remotely, so provider traces alone may not distinguish hosted inference from local weights.

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
is why figure descriptions are generated in the background for retrieval, while remaining private
and absent from the reader. `GENERATE_FIGURE_DESCRIPTIONS` must use a vision-capable model;
DeepSeek has no vision, so with it active figures cannot be described.

---

## 97. `/models` list and per-request override

**What it does.** The shared picker lists local and cloud models for article notes and desk study
questions. Available models appear first. Models known to be unavailable remain visible, disabled,
with a short reason.

**Where.** [`llm/catalog.py`](../../backend/app/llm/catalog.py), [`llm/availability.py`](../../backend/app/llm/availability.py),
`GET /models`, [`ModelPicker.tsx`](../../frontend/src/components/ModelPicker.tsx),
`paper_notes.requested_model`, `llm_client.stream_chat(model=…)`.

**How it works.** The catalog reads Ollama's `/api/tags`, filters embedding models, and marks tags
cloud-hosted when `OLLAMA_BASE_URL` points to a remote endpoint (local Ollama addresses remain
local; cloud suffixes and zero-size tags are cloud signals too). It includes provider-pinned models
when their provider is configured; `meta/muse-glimmer-30b` is pinned to NVIDIA and shown as
“Muse Glimmer 30B (NVIDIA)”. Real chat outcomes are cached in Redis for six hours: success marks a
model available, while HTTP 402, 401, 403, and 404 mark it unavailable with a provider-specific
reason. The catalog does not probe models. Unknown cache state or Redis failure means available,
so `/models` stays responsive and a real request decides. The selected model is persisted on the
note and inherited by follow-ups. If it fails before streamed output, the default route may answer
with a visible notice; the answer metadata names the model that actually responded.

**Why it does not break the no-hardcoding rule.** The user selects a catalog entry and its model ID
remains the request value; the picker label is presentation only. `MODEL_PROVIDER_PINS` is the
explicit provider-ownership mapping, not a default model setting.

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
