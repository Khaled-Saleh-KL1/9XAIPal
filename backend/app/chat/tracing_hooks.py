"""Output hooks that turn chat results into readable trace blocks."""

from typing import Any

from app.core import tracing


def retrieval_output(result: Any) -> Any:
    """Process retrieval results, handling wrapped items from search_figure_chunks."""
    chunks = result if isinstance(result, (list, tuple)) else (result or {}).get("chunks") if isinstance(result, dict) else None
    if isinstance(chunks, (list, tuple)):
        # Unwrap items that have a "chunk" key (from search_figure_chunks)
        unwrapped = []
        for item in chunks:
            if isinstance(item, dict) and "chunk" in item and isinstance(item["chunk"], dict):
                unwrapped.append(item["chunk"])
            else:
                unwrapped.append(item)
        tracing.set_attributes(**tracing.retrieval_attributes(unwrapped))
        return {"chunks": len(chunks)}
    return result


def context_output(result: Any) -> Any:
    """Process context builder results, handling various list/array keys and formats."""
    if isinstance(result, dict):
        # Process each list key, mapping items to retrieval format as needed
        for key in ("chunks", "sections", "results", "section_summaries"):
            items = result.get(key)
            if isinstance(items, (list, tuple)):
                if key == "section_summaries":
                    # Map section summaries to retrieval format using heading_path as id and summary_plain as text
                    mapped = []
                    for item in items:
                        if isinstance(item, dict):
                            mapped.append({
                                "id": item.get("heading_path", item.get("id", "")),
                                "plain_text": item.get("summary_plain", ""),
                            })
                    if mapped:
                        tracing.set_attributes(**tracing.retrieval_attributes(mapped))
                elif key == "results":
                    # Map web search results (title, url, snippet, score) to retrieval format
                    mapped = []
                    for item in items:
                        if isinstance(item, dict) and "url" in item:
                            mapped.append({
                                "id": item["url"],
                                "plain_text": f"{item.get('title', '')}\n{item.get('snippet', '')}",
                                "score": item.get("score"),
                            })
                    if mapped:
                        tracing.set_attributes(**tracing.retrieval_attributes(mapped))
                else:
                    # Standard retrieval format (chunks with id and plain_text)
                    # Unwrap items from search_figure_chunks that have "chunk" key
                    unwrapped = []
                    for item in items:
                        if isinstance(item, dict) and "chunk" in item and isinstance(item["chunk"], dict):
                            unwrapped.append(item["chunk"])
                        else:
                            unwrapped.append(item)
                    tracing.set_attributes(**tracing.retrieval_attributes(unwrapped))
        return {k: (f"<{len(v)} items>" if isinstance(v, (list, tuple)) else v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return retrieval_output(result)
    return result


def ask_stream_output(events: list) -> Any:
    for event in reversed(events):
        if isinstance(event, dict) and event.get("answer"):
            return event["answer"]
    return f"<{len(events)} stream events>"
