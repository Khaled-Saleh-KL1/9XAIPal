"""Helpers for reading JSON out of chat-model replies."""


def strip_code_fence(content: str) -> str:
    """Unwrap a ```json ... ``` fence if the model added one.

    gemma4:31b wraps JSON replies in a markdown fence even when the prompt asks
    for JSON only, and json.loads() rejects the fenced text outright.
    """
    s = (content or "").strip()
    if not s.startswith("```"):
        return s
    s = s.split("\n", 1)[1] if "\n" in s else ""      # drop the ```json line
    end = s.rfind("```")
    return (s[:end] if end != -1 else s).strip()
