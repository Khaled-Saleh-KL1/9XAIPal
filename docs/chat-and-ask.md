# Chat & /ask

The chat is the second half of the product. The reading view shows
structural chunks one at a time. The `/ask` endpoint takes a user
question, **decides what kind of context to retrieve**, builds that
context, calls a local LLM, and returns a grounded answer with citations.

## The four context modes

| Context  | When                                                       | What we retrieve                              | Model receives                                                |
| -------- | ---------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------- |
| LOCAL    | The question is about what's currently on screen           | The current chunk (± neighbors) + its image   | `system + "Context:\n<chunks>"` + image as base64 (multimodal) |
| GLOBAL   | The question requires searching the whole paper            | Top-K similar chunks by pgvector cosine       | `system + "Context:\n<chunks with similarity scores>"`        |
| OVERVIEW | The question is paper-level ("summarize the paper")        | Pre-computed section_summaries (hierarchical) | `system + "Context:\n<structured outline>"`                   |
| EXTERNAL | The question is about something outside the paper          | Top-K web results from SearXNG                | `system + "Context:\n<title/url/snippet rows>"`               |

## The flow ([chat/orchestrator.py](../backend/app/chat/orchestrator.py))

```python
async def handle_ask(session, *, prompt, document_id, current_chunk_id, conversation_id):
    # Step 0: Topic guardrail (is this about IT/CS?)
    guardrail_result = await check_guardrail(prompt, in_paper_context=...)

    # Step 1: Route the prompt
    decision = await route_prompt(prompt, has_current_chunk=..., has_document=...)
    # decision.context_type ∈ {LOCAL, GLOBAL, OVERVIEW, EXTERNAL}

    # Step 2: Build context
    if LOCAL:
        ctx = build_local_context(session, document_id, current_chunk_id, window_size=1)
        context_text = format_local_context(ctx["chunks"])
        citations = citations_from_chunks(ctx["chunks"])
        image_paths = [asset.file_path for asset in ctx["assets"] if asset_type=="image"]
        system_prompt = LOCAL_SYSTEM_PROMPT

    elif GLOBAL:
        ctx = build_global_context(session, query=prompt, document_id, limit=3)
        context_text = format_global_context(ctx["chunks"])
        citations = citations_from_chunks(ctx["chunks"])
        system_prompt = GLOBAL_SYSTEM_PROMPT

    elif OVERVIEW:
        ctx = build_overview_context(session, document_id)
        context_text = format_overview_context(ctx["summaries"])
        citations = citations_from_summaries(ctx["summaries"])
        system_prompt = OVERVIEW_SYSTEM_PROMPT

    else:  # EXTERNAL
        ctx = build_external_context(prompt, max_results=5)
        context_text = format_external_context(ctx["results"])
        citations = citations_from_web_results(ctx["results"])
        system_prompt = EXTERNAL_SYSTEM_PROMPT

    # Step 3: Build multimodal messages
    messages = build_multimodal_messages(prompt, system=system_prompt,
                                         context_text=context_text,
                                         image_paths=image_paths)

    # Step 4: Call LLM
    result = await ollama_client.chat(messages)

    # Step 5: If the model signals NEEDS_RESEARCH, run the research agent loop
    if result.get("needs_research"):
        result = await research_agent.loop(prompt, document_id)

    # Step 6: Persist turn + trace
    return AskResponse(answer, context_type, router_reason, citations, model, conversation_id)
```

## The router ([chat/router.py](../backend/app/chat/router.py))

Two-tier:

1. **Cheap heuristic first.** Three keyword lists:
   - `_LOCAL_KEYWORDS` — `"this formula"`, `"this figure"`, `"above"`,
     `"shown here"`, `"bring a picture"`, etc. Only fires if the request
     carries a `current_chunk_id`.
   - `_OVERVIEW_KEYWORDS` — `"summarize the paper"`, `"main contribution"`,
     `"tl;dr"`, `"executive summary"`, etc.
   - `_EXTERNAL_KEYWORDS` — `"latest"`, `"recent"`, `"who is"`,
     `"wikipedia"`, etc.
2. **Fallback to LLM** for ambiguous queries: outputs `LOCAL`, `GLOBAL`,
   `OVERVIEW`, or `EXTERNAL` as JSON.

Special cases:
- No document context → EXTERNAL wins.
- LLM routing failure → default is GLOBAL when document exists.
- Sub-threads default to paper-free (`is_sub_thread=True`).

Each decision carries a `reason` string stored in `ask_traces.router_reason`.

## LOCAL context ([chat/local_context.py](../backend/app/chat/local_context.py))

1. Fetches a **window** of chunks centered on `current_chunk_id` (default ±1).
2. Fetches every `chunk_assets` row for any chunk in the window.
3. Images are base64-encoded and attached to the Ollama user message.

If the current chunk is a figure, the model literally sees the picture.

## GLOBAL context ([chat/global_context.py](../backend/app/chat/global_context.py))

1. Calls `get_query_embedding(prompt)` — produces a 768-dim vector.
2. Calls `embeddings.search_embeddings` — pgvector cosine-similarity.
3. Returns top-K (default 3) chunks.
4. Surfaces images attached to retrieved chunks for inline rendering.

## OVERVIEW context ([chat/overview_context.py](../backend/app/chat/overview_context.py))

1. Fetches all `section_summaries` rows (level 0 + level 1 + level 2).
2. Formats them as a structured document outline.
3. Citations come from each summary's `source_chunk_ids`.

## EXTERNAL context ([chat/external_context.py](../backend/app/chat/external_context.py))

1. Calls SearXNG with the raw prompt.
2. Ranks results via `search/ranking.py` (dedup + scoring).
3. Returns at most 5 results.

## Research Agent ([chat/research_agent.py](../backend/app/chat/research_agent.py))

For complex queries, an iterative research loop:
- Tools: `web_search`, `read_paper_section`, `describe_figure`.
- Maintains a research log and synthesizes a final response.
- Triggered when the LLM emits a `NEEDS_RESEARCH` signal.

## Multimodal request shape ([llm/multimodal.py](../backend/app/llm/multimodal.py))

```python
messages = [
  {"role": "system", "content": <prompt>},
  {"role": "user",
   "content": "Context:\n<context_text>\n\n<original prompt>",
   "images": ["<base64 PNG/JPEG>", ...]},   # only LOCAL with images
]
```

The client POSTs to `{OLLAMA_BASE_URL}/api/chat` with `stream: false`.

## Citations ([chat/citations.py](../backend/app/chat/citations.py))

Two builders:
- `citations_from_chunks` → `chunk_id`, `sequence_id`, `page`, `text_snippet`, `source="document"`.
- `citations_from_web_results` → `url`, `text_snippet`, `source=<engine>`.

Persisted as JSON on the assistant's `conversation_turns` row.

## Conversation continuity

`conversation_id` is optional on first turn. If absent, the orchestrator
mints a new UUID. The frontend stores and passes it on every subsequent
`/ask` call.

## Sub-threads

Turns can have a `parent_turn_id` creating a tree of sub-threads.
`get_thread_subtree(root_turn_id)` fetches only the sub-thread's turns
using a recursive CTE. Sub-threads default to paper-free context.

## Compaction

When a conversation grows past a token threshold, it is automatically
compacted: earlier turns are summarized into a compact form and replaced
with a single `role='compaction'` turn. This keeps context from overflowing.

## Tracing

Every `/ask` call inserts an `ask_traces` row with:
`context_type`, `router_reason`, `model`, `prompt_tokens`,
`completion_tokens`, `latency_ms`.