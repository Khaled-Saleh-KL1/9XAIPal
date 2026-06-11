"""Context router: classifies prompts into LOCAL, GLOBAL, EXTERNAL, or OVERVIEW.

OVERVIEW is special: it triggers the pre-computed high-quality hierarchical
section summaries (and paper-level executive overview) instead of vector search.
This path exists to give the author the best possible answers to
"what is this paper about?" style questions.
"""

import json
import re
from dataclasses import dataclass
from typing import Optional

from app.llm import client as llm_client
from app.chat.prompts import ROUTER_SYSTEM_PROMPT


_VALID_CONTEXTS = {"LOCAL", "GLOBAL", "OVERVIEW", "EXTERNAL"}


def _parse_router_json(text: str) -> Optional[dict]:
    """Extract the first JSON object that has a valid context_type field."""
    if not text:
        return None
    # Direct parse
    try:
        obj = json.loads(text)
    except Exception:
        # Find the first {...} block
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    ctx = str(obj.get("context_type", "")).strip().upper()
    if ctx not in _VALID_CONTEXTS:
        return None
    return {
        "context_type": ctx,
        "reason": obj.get("reason") or "",
        "confidence": float(obj.get("confidence", 1.0)) if obj.get("confidence") is not None else 1.0,
    }


@dataclass
class RouterDecision:
    context_type: str  # LOCAL, GLOBAL, EXTERNAL, OVERVIEW
    reason: str
    confidence: float = 1.0


# Keywords that indicate LOCAL context (refers to visible content)
_LOCAL_KEYWORDS = [
    "this formula", "this equation", "this chart", "this figure", "this image",
    "this table", "this paragraph", "this section", "here", "above", "below",
    "what does this", "explain this", "what is this", "the current",
    "on screen", "visible", "this page", "shown here",
    "bring a picture", "bring me a picture", "show me a picture", "show a picture",
    "show the figure", "show me the figure", "display the figure", "display the image",
    "show the image", "show me the image", "their shapes", "this diagram", "this picture",
]

# Keywords indicating EXTERNAL context (web/current events)
_EXTERNAL_KEYWORDS = [
    "latest", "recent", "news", "today", "current events", "2025", "2026",
    "who is", "what happened", "search the web", "find online", "look up",
    "wikipedia", "according to",
]

# Strong signals for "give me the high-level view of this paper"
# These trigger the pre-computed section summaries path (OVERVIEW).
_OVERVIEW_KEYWORDS = [
    "summarize the paper", "summarize this paper", "paper summary",
    "what is this paper about", "what is this about", "what's this paper about",
    "big picture", "high level", "high-level", "overview of the paper",
    "main contribution", "key contributions", "core contribution",
    "tl;dr", "tldr", "too long didn't read", "executive summary",
    "walk me through the paper", "structure of the paper", "outline of the paper",
    "what does the paper argue", "what is the paper's main point",
    "give me an overview", "give an overview", "paper overview",
    "in a nutshell", "bottom line", "key takeaway", "key takeaways",
    "what are the main findings", "main results of the paper",
]


async def route_prompt(
    prompt: str,
    *,
    has_current_chunk: bool = False,
    has_document: bool = False,
) -> RouterDecision:
    """Classify a prompt into Local, Global, or External context.

    Step 1: Heuristic keyword matching for fast routing.
    Step 2: If ambiguous, use LLM for classification.
    """
    lower = prompt.lower().strip()

    # Step 1: LOCAL — user references the visible chunk/image
    if has_current_chunk:
        for kw in _LOCAL_KEYWORDS:
            if kw in lower:
                return RouterDecision(
                    context_type="LOCAL",
                    reason=f"Query references visible content (matched: '{kw}')",
                    confidence=0.9,
                )

    # Step 1 (OVERVIEW): High-value pre-computed path for paper-level questions.
    # This bypasses vector search and uses the rich hierarchical summaries.
    for kw in _OVERVIEW_KEYWORDS:
        if kw in lower:
            return RouterDecision(
                context_type="OVERVIEW",
                reason=f"Overview / paper-level summary request (matched: '{kw}')",
                confidence=0.95,
            )

    # Step 1: EXTERNAL — user asks about external/web information
    for kw in _EXTERNAL_KEYWORDS:
        if kw in lower:
            return RouterDecision(
                context_type="EXTERNAL",
                reason=f"Query targets external/web information (matched: '{kw}')",
                confidence=0.85,
            )

    # If no document context at all, route external
    if not has_document and not has_current_chunk:
        return RouterDecision(
            context_type="EXTERNAL",
            reason="No document context available, routing to web search",
        )

    # Step 2: Use LLM for ambiguous cases
    try:
        context_info = ""
        if has_current_chunk:
            context_info = "The user is viewing a specific chunk of the paper."
        if has_document:
            context_info += " A full paper is available for vector search."

        messages = [
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Context: {context_info}\nUser query: {prompt}"},
        ]

        # Classification is a tiny task: use the (optionally smaller) classifier
        # model and cap output so it can't generate a long explanation.
        result = await llm_client.chat(
            messages,
            role="classifier",
            temperature=0.1,
            num_predict=64,
        )
        raw = (result["content"] or "").strip()

        # New ROUTING_PROMPT outputs JSON: {"context_type":..., "reason":..., "confidence":...}
        # Fall back to first-token parsing if JSON is malformed.
        parsed = _parse_router_json(raw)
        if parsed:
            return RouterDecision(
                context_type=parsed["context_type"],
                reason=parsed.get("reason") or raw,
                confidence=parsed.get("confidence", 1.0),
            )

        upper = raw.upper()
        if upper.startswith("LOCAL"):
            return RouterDecision(context_type="LOCAL", reason=raw)
        if upper.startswith("EXTERNAL"):
            return RouterDecision(context_type="EXTERNAL", reason=raw)
        if upper.startswith("OVERVIEW"):
            return RouterDecision(context_type="OVERVIEW", reason=raw)
        return RouterDecision(context_type="GLOBAL", reason=raw)

    except Exception:
        pass

    # Default: GLOBAL if document available (OVERVIEW is only taken via explicit keyword match above)
    if has_document:
        return RouterDecision(
            context_type="GLOBAL",
            reason="Default to global document vector search",
        )
    return RouterDecision(context_type="EXTERNAL", reason="Fallback to external search")

