"""Topic guardrail: restrict /ask to IT-related questions."""

from app.llm import ollama_client
from app.core.config import settings
from app.core.logging import get_logger
from app.chat.prompts import GUARDRAIL_PROMPT

logger = get_logger(__name__)

# Back-compat alias (kept so anything importing the old name still works).
GUARDRAIL_SYSTEM_PROMPT = GUARDRAIL_PROMPT


async def is_topic_allowed(prompt: str, *, in_paper_context: bool = False) -> bool:
    """Return True if the prompt is IT-related (or IT-in-another-sector).

    ``in_paper_context`` flips on when the user is reading a paper (a
    ``document_id`` was passed to /ask). The classifier still rejects pure
    medical / non-IT-engineering questions, but generic paper-grounded prompts
    like "describe this figure" or "what's in this table?" are treated as
    in-scope because the surrounding document is itself IT.
    """
    # Fast path: when reading a paper, the document is itself IT and paper-
    # grounded questions are in-scope by definition. Skipping the LLM here
    # removes a full model call from the critical path of every paper question.
    if in_paper_context and settings.guardrail_skip_in_paper:
        return True

    user_content = prompt
    if in_paper_context:
        user_content = (
            "[Context: the user is currently reading an IT research paper. "
            "Generic prompts that reference 'this figure / table / equation / "
            "section / paper / page' are in-scope.]\n\n"
            f"Question: {prompt}"
        )
    messages = [
        {"role": "system", "content": GUARDRAIL_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        # Verdict is one word — use the cheap classifier model and cap output.
        result = await ollama_client.chat(
            messages,
            model=settings.effective_classifier_model,
            temperature=0.0,
            num_predict=8,
        )
    except Exception:
        logger.exception("guardrail LLM call failed; failing open (allow)")
        return True

    # New prompt outputs ALLOWED / BLOCKED. Anything that isn't a clear ALLOWED
    # (including legacy OUT_OF_SCOPE) is treated as blocked.
    verdict = (result.get("content") or "").strip().upper()
    allowed = verdict.startswith("ALLOWED")
    logger.info(
        "guardrail verdict=%s allowed=%s in_paper=%s",
        verdict[:40], allowed, in_paper_context,
    )
    return allowed
