"""Topic guardrail: restrict /ask to IT-related questions."""

from app.llm import client as llm_client
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
        result = await llm_client.chat(
            messages,
            role="classifier",
            temperature=0.0,
            num_predict=8,
        )
    except Exception:
        logger.exception("guardrail LLM call failed; failing open (allow)")
        return True

    # ⚠ A cut-off reply is NOT a verdict.
    #
    # "Anything that isn't a clear ALLOWED is blocked" is the right rule for
    # something the model actually said — but an empty string is what comes
    # back when the model never got to speak, and treating that as BLOCKED
    # rejects the user's question for a reason that has nothing to do with
    # the question. A reasoning model makes this the DEFAULT outcome: it
    # spends the whole cap thinking, so content is "" and finish_reason is
    # "length" (this is exactly what meta/muse-glimmer-30b did against the
    # old num_predict=8 — every question would have been blocked).
    #
    # So a non-answer fails OPEN, matching what the `except` above already
    # does for a call that raised: an over-permissive guardrail is a bad day,
    # a guardrail that blocks everything is a broken app.
    verdict = (result.get("content") or "").strip().upper()
    if not verdict or result.get("finish_reason") == "length":
        logger.warning(
            "guardrail returned no verdict (finish_reason=%s, content=%r); failing open",
            result.get("finish_reason"), (result.get("content") or "")[:40],
        )
        return True

    allowed = verdict.startswith("ALLOWED")
    logger.info(
        "guardrail verdict=%s allowed=%s in_paper=%s",
        verdict[:40], allowed, in_paper_context,
    )
    return allowed
