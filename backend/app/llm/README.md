# LLM Design

## Purpose

The `llm` directory owns all model communication — local Ollama **and** cloud
APIs (OpenAI, Anthropic, Gemini, xAI, DeepSeek). Other backend layers never
know which backend is active or what the model is called: they call the
client with a *role* and the resolver picks the backend and model.

## Files

### `resolver.py`

The single decision point for "which AI backend and which model?". With
`LLM_PROVIDER=auto` (default):

1. Probe Ollama at `OLLAMA_BASE_URL` (`GET /api/tags`, 3 s timeout, result
   cached 30 s). Reachable → use Ollama with `CHAT_MODEL` / `VLM_MODEL` /
   `CLASSIFIER_MODEL`.
2. Otherwise walk the cloud keys in fixed order — OpenAI → Anthropic →
   Gemini → xAI → DeepSeek — and use the first `*_API_KEY` that is set, with
   that provider's own model setting (`OPENAI_CHAT_MODEL`, …). Ollama tags
   are never sent to a cloud API.
3. Otherwise raise `NoLLMConfigured` (→ HTTP 503, code `NO_LLM_CONFIGURED`)
   with verbatim instructions to put an API key or an Ollama connection in
   `backend/.env`.

`LLM_PROVIDER` can also pin one backend explicitly (`ollama`, a cloud name,
or `custom` = any OpenAI-compatible endpoint via `LLM_BASE_URL`).

Exposes `resolve_llm()` / `resolve_llm_sync()` returning a frozen
`LLMTarget` (provider, key, base URL, chat/classifier/vlm models, and
`model_for_role(role)`), plus `resolve_embedding()` / `resolve_embedding_sync()`
returning an `EmbeddingTarget`. Embedding resolution follows the same chain
but only OpenAI and Gemini offer embedding APIs, and the auto choice is
**pinned per process** — vectors from different models are not comparable,
so a mid-run Ollama hiccup must never mix models inside one library.

### `client.py`

Backend-agnostic entry points used by the rest of the app: `chat`,
`stream_chat` (async) and `chat_sync` (Celery workers). Callers pass
`role="chat" | "classifier" | "vlm"` (or an explicit `model` override); the
client resolves the target, then dispatches — Ollama targets through
`ollama_client.py`, cloud targets via OpenAI-compatible
`POST {base_url}/chat/completions` with a Bearer key (keyless `custom`
endpoints allowed). `is_available()` returns False when resolution fails.

### `ollama_client.py`

Ollama-specific transport: `POST /api/chat` (+ streaming) and `/api/tags`.
Long read timeout (600 s) so a cold-started big model doesn't die at 120 s.

### `multimodal.py`

Builds multimodal requests from chunks and assets, attaches extracted images
as base64, and keeps payloads within model limits.

## Data Dependencies

`llm` depends on `core.config` (every parameter is `.env`-driven; nothing is
hardcoded).

`chat`, `services.reading_order`, `summarization`, and `embeddings` depend on
`llm` (the summarizers resolve the model name upfront because it keys their
idempotency hashes and stored rows).
