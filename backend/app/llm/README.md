# LLM Design

## Purpose

The `llm` directory owns local model communication. Other backend layers should not know Ollama request details.

## Files

### `ollama_client.py`

Wraps Ollama chat and generation APIs. The default chat model is `gemma4:26b`.

### `vlm_client.py`

Wraps the local vision-language model used for initial processing, figure explanation, diagram interpretation, and optional OCR fallback.

### `model_registry.py`

Centralizes local model names and capabilities such as text, vision, math, long context, and embeddings.

### `multimodal.py`

Builds multimodal requests from chunks and assets, attaches extracted images, and keeps payloads within local model limits.

## Data Dependencies

`llm` depends on `core.config`.

`chat` depends on `llm`.

`extraction` may optionally depend on `llm.vlm_client` for image enrichment, but that path should remain isolated.

