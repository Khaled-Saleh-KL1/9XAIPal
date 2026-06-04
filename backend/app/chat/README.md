# Chat Design

## Purpose

The `chat` directory owns prompt routing, context assembly, model invocation orchestration, and citation formatting.

The `/ask` endpoint delegates to this layer instead of deciding retrieval strategy itself.

## Files

### `router.py`

Classifies prompts into `Local`, `Global`, or `External` context. Router output should include selected context, confidence, reason, and fallback policy.

### `orchestrator.py`

Coordinates the `/ask` workflow: validate context inputs, route the prompt, build context, call the selected model path, attach citations, store trace data, and return the answer.

### `prompts.py`

Stores system prompts and templates for local answers, global RAG answers, external web answers, multimodal answers, and routing.

### `local_context.py`

Builds context from the currently visible chunk, nearby chunks, attached images, page metadata, and heading information.

### `global_context.py`

Builds context using vector retrieval across PostgreSQL. It embeds the user query, searches pgvector, fetches matching chunks, and prepares cited context blocks.

### `external_context.py`

Builds context from SearXNG results when the user asks for current, recent, or out-of-document information.

### `citations.py`

Formats citations for chunk IDs, sequence IDs, pages, image assets, and external URLs.

## Routing Rules

- `Local`: prompts about the chunk currently on screen.
- `Global`: prompts about the full document, related sections, definitions, comparisons, or earlier/later material.
- `External`: prompts about current events, recent facts, web facts, or information not expected to be in the document.

## Data Dependencies

`chat.local_context` depends on `database.repositories.chunks`.

`chat.global_context` depends on `services.retrieval`, `embeddings.service`, and `database.pgvector` through the repository layer.

`chat.external_context` depends on `search.searxng_client` and `search.ranking`.

`chat.orchestrator` depends on `llm.ollama_client`, `llm.multimodal`, and `database.repositories.conversations`.
