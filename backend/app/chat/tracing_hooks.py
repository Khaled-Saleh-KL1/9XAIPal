"""Output hooks that turn chat results into readable trace blocks."""

from typing import Any

from app.core import tracing


def retrieval_output(result: Any) -> Any:
    chunks = result if isinstance(result, (list, tuple)) else (result or {}).get("chunks") if isinstance(result, dict) else None
    if isinstance(chunks, (list, tuple)):
        tracing.set_attributes(**tracing.retrieval_attributes(list(chunks)))
        return {"chunks": len(chunks)}
    return result


def context_output(result: Any) -> Any:
    if isinstance(result, dict):
        for key in ("chunks", "sections", "results"):
            if isinstance(result.get(key), (list, tuple)):
                tracing.set_attributes(**tracing.retrieval_attributes(list(result[key])))
        return {k: (f"<{len(v)} items>" if isinstance(v, (list, tuple)) else v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return retrieval_output(result)
    return result


def ask_stream_output(events: list) -> Any:
    for event in reversed(events):
        if isinstance(event, dict) and event.get("answer"):
            return event["answer"]
    return f"<{len(events)} stream events>"
