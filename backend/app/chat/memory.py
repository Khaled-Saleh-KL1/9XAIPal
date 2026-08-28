"""Cross-session memory for the paper/book/study agents.

Durable, embedded observations about a reader — a stated preference, their
level of expertise, a recurring interest — kept in ``agent_memories`` and
retrieved by semantic similarity to the current question. This is the one
place in the chat stack that deliberately DOES lean on pgvector: paper_agent's
own docstring notes it stays off pgvector for PAPER content, because
similarity search returns passages *about* a topic rather than the one that
states it. That reason does not apply here — "roughly the same meaning" is
exactly what a memory lookup wants, since a reader who once said "keep
answers short" phrases it differently every time.

Two ways a memory gets written, both funnelled through ``write_memory`` so
dedup only has to live in one place:

  explicit   the agent chose to note it mid-conversation — ``REMEMBER:`` in a
             tool block, or a ``<remember>...</remember>`` tag on a turn that
             has no tools left (see agent_tools.extract_remembers — same
             fallback ``sticky_notes`` uses for a stray ``<note>`` tag: a
             model reaches for the marker whether the current turn offers it
             as a tool or not).
  distilled  extracted after the fact from a compacted conversation
             (``distill_memories``, called from orchestrator's compaction
             hook) — what lets memory grow on its own, for the reader who is
             consistent turn to turn but never explicitly says "remember
             this".

``document_id=None`` means the memory applies for this reader everywhere;
set, it only surfaces while they are in that paper/book. ``recall_memories``
always checks both. Explicit writes are always global — the model has no
clean way to say "this is specific to this book" from inside a one-line
REMEMBER, and reader-level preferences are the common case there anyway;
document-scoped nuance is left to distillation, which sees the whole
conversation and can judge.
"""

import re
from uuid import UUID
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agent_tools import step_event
from app.core.logging import get_logger
from app.database.repositories import memories as memory_repo
from app.embeddings.model import active_embedding_model, get_embeddings_batch
from app.llm import client as llm_client

logger = get_logger(__name__)

# Above this cosine similarity, a candidate is treated as "already known"
# rather than written again — a reader who mentions the same preference three
# conversations running should produce one memory, not three.
_DEDUP_SIMILARITY = 0.92

# Below this, a stored memory is not relevant enough to spend prompt space on.
_RECALL_SIMILARITY = 0.45

_MAX_BODY_CHARS = 500


async def recall_memories(
    session: AsyncSession,
    *,
    user_id: UUID,
    document_id: Optional[UUID],
    question: str,
    limit: int = 5,
) -> list[dict]:
    """The reader's most relevant memories for this question, if any.

    Degrades to "no memories" rather than raising — a dead embedding provider
    must cost personalization, not the answer.
    """
    try:
        [embedding] = await get_embeddings_batch([question])
    except Exception:
        logger.warning("memory recall: embedding the question failed", exc_info=True)
        return []
    return await memory_repo.recall(
        session,
        user_id=user_id,
        document_id=document_id,
        query_embedding=embedding,
        limit=limit,
        min_similarity=_RECALL_SIMILARITY,
    )


def format_memories(rows: list[dict]) -> str:
    """Render recalled memories as a context block, or '' when there are none."""
    if not rows:
        return ""
    lines = "\n".join(f"- {r['body']}" for r in rows)
    return (
        "WHAT YOU'VE LEARNED ABOUT THIS READER (from past conversations — may "
        f"or may not be relevant here):\n{lines}"
    )


async def write_memory(
    session: AsyncSession,
    *,
    user_id: UUID,
    body: str,
    document_id: Optional[UUID] = None,
    source: str = "explicit",
) -> Optional[UUID]:
    """Store one durable memory, unless a near-duplicate already exists in
    this exact scope. Commits on success — callers stream, and a memory a
    later step in the same request reads back (via recall) must already be
    visible, not stuck in an uncommitted transaction.
    """
    body = " ".join((body or "").split())
    if not body or len(body) > _MAX_BODY_CHARS:
        return None
    try:
        [embedding] = await get_embeddings_batch([body])
    except Exception:
        logger.warning("memory write: embedding failed", exc_info=True)
        return None

    dup = await memory_repo.find_duplicate(
        session,
        user_id=user_id,
        document_id=document_id,
        query_embedding=embedding,
        min_similarity=_DEDUP_SIMILARITY,
    )
    if dup:
        logger.info("memory: skipped near-duplicate of %s", dup["id"])
        return None

    model_name = await active_embedding_model()
    memory_id = await memory_repo.remember(
        session,
        user_id=user_id,
        document_id=document_id,
        body=body,
        embedding=embedding,
        model_name=model_name,
        source=source,
    )
    await session.commit()
    logger.info("memory[%s] stored for user=%s doc=%s: %.80s", source, user_id, document_id, body)
    return memory_id


async def write_remembered(
    session: AsyncSession,
    bodies: list[str],
    *,
    user_id: UUID,
    n: int,
    id_prefix: str,
    trail: list[dict],
) -> AsyncIterator[dict]:
    """Store each body from a REMEMBER tag fallback and report it as a step.

    Mirrors study_agent's ``_pin_written_notes``: reached only from the paths
    that never go through the tool dispatcher (the model answered straight
    from the probe, or this is the forced final turn), because REMEMBER lines
    inside a real ``<tool>`` block are already handled there.
    """
    for i, body in enumerate(bodies):
        memory_id = await write_memory(session, user_id=user_id, body=body, document_id=None, source="explicit")
        call = {
            "tool": "REMEMBER",
            "arg": body,
            "label": "Remembered that for next time",
            "result": "saved" if memory_id else "already knew that",
        }
        event = step_event(f"{id_prefix}-{i}", n, call, state="done", think=None)
        trail.append({k: v for k, v in event.items() if k != "type"})
        yield event


_DISTILL_PROMPT = """Below is a conversation between a reader and an AI answering \
questions about a document they're reading. Extract at most 3 short, durable \
observations about the READER worth remembering for future conversations — a \
stated preference, their level of expertise, a recurring interest or \
confusion, a correction they made. Do not summarize the document or the \
answers; only what says something about how to help this reader specifically.

Skip it entirely if there is nothing durable — most conversations produce \
nothing worth keeping.

Each observation on its own line, tagged with its scope:
[GLOBAL] the reader always prefers short, non-technical answers
[DOCUMENT] the reader is working through this book's timeline chapter by chapter

GLOBAL is for anything true of the reader regardless of what they're reading. \
DOCUMENT is for something that only makes sense about this specific paper/book.

CONVERSATION:
{conversation_text}

OBSERVATIONS (or nothing if none apply):"""

_DISTILL_LINE_RE = re.compile(r"^\s*\[(GLOBAL|DOCUMENT)\]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


async def distill_memories(
    session: AsyncSession,
    *,
    user_id: UUID,
    document_id: Optional[UUID],
    conversation_text: str,
) -> int:
    """Ask the model what's worth remembering from a conversation and store it.

    This is what lets memory grow without the reader or the agent explicitly
    saying "remember this" every time — called from the same compaction
    checkpoint that already re-reads the transcript every ~5 turns, so it
    costs one more model call on a request that was already paying for one.
    """
    prompt = _DISTILL_PROMPT.format(conversation_text=conversation_text[:6000])
    try:
        result = await llm_client.chat(
            [
                {
                    "role": "system",
                    "content": "You extract durable facts about a reader from a conversation transcript.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
    except Exception:
        logger.warning("memory distillation LLM call failed", exc_info=True)
        return 0

    text = (result.get("content") or "").strip()
    stored = 0
    for scope, body in _DISTILL_LINE_RE.findall(text):
        scoped_doc_id = document_id if scope.upper() == "DOCUMENT" else None
        memory_id = await write_memory(
            session, user_id=user_id, body=body, document_id=scoped_doc_id, source="distilled"
        )
        if memory_id:
            stored += 1
    return stored
