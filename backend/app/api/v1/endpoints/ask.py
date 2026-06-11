"""Ask endpoint: routes prompts through context router to local LLM."""

import json
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_ask_limiter, get_ask_semaphore
from app.api.errors import DocumentNotFound, ModelUnavailable, NoLLMConfigured
from app.chat.orchestrator import handle_ask, handle_ask_stream
from app.core.logging import get_logger
from app.services import documents as doc_service
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import conversations as conv_repo

logger = get_logger(__name__)

router = APIRouter()

# Hard cap on sub-thread nesting: main chat (layer 0) + 3 sub-chat layers.
# Tested up to L3; deeper nesting is disabled in the UI and rejected by /ask.
MAX_SUB_THREAD_DEPTH = 3


class AskPayload(BaseModel):
    query: str
    current_sequence_order: Optional[int] = None
    conversation_id: Optional[UUID] = None

    # Richer visible context (quality improvement)
    # Allows the model to "see" what the user currently has open in the paper viewer
    visible_sequence_orders: Optional[list[int]] = None   # e.g. [42, 43, 44, 45]
    focused_element: Optional[str] = None                 # "figure:7", "table:3", "architecture-diagram", etc.

    # Optional image attachments uploaded with this question (e.g. user drops a
    # screenshot of a figure into the chat). Base64-encoded raw bytes, no
    # `data:image/...;base64,` prefix. Passed through to the multimodal Ollama
    # request so vision-capable models like gemma4 can actually see the image.
    images_b64: Optional[list[str]] = None

    # === Sub-thread (nested tangent) support ===
    # parent_turn_id: the turn this message is a reply to inside a sub-thread.
    # thread_root_turn_id: the root of the sub-thread the user is currently in
    # (the original branching user turn). Used for correct history + compaction.
    parent_turn_id: Optional[UUID] = None
    thread_root_turn_id: Optional[UUID] = None


async def _resolve_ask_target(
    db: AsyncSession, paper_id: UUID, payload: AskPayload
) -> tuple[dict, Optional[UUID]]:
    """Shared /ask pre-checks: 404, sub-thread depth cap, chunk resolution."""
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    # Sub-thread depth cap: a sub-thread rooted at R has depth = chain_len(R) + 1.
    # Block any /ask whose target sub-thread would exceed MAX_SUB_THREAD_DEPTH.
    # This is defense-in-depth: the UI also hides the "Thread →" affordance at L3.
    if payload.thread_root_turn_id is not None:
        root_chain = await conv_repo.compute_turn_depth(db, payload.thread_root_turn_id)
        sub_depth = root_chain + 1
        if sub_depth > MAX_SUB_THREAD_DEPTH:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Sub-thread nesting limit reached (max {MAX_SUB_THREAD_DEPTH} levels). "
                    "Reply in the current thread or step back to a shallower level."
                ),
            )

    # Resolve current_chunk_id from sequence_order if provided
    current_chunk_id = None
    if payload.current_sequence_order is not None:
        chunk = await chunk_repo.get_chunk_by_sequence(
            db, paper_id, payload.current_sequence_order
        )
        if chunk:
            current_chunk_id = chunk["id"]

    return doc, current_chunk_id


@router.post("/{paper_id}/ask")
async def ask_paper(
    paper_id: UUID,
    payload: AskPayload,
    db: AsyncSession = Depends(get_db),
    _limiter: None = Depends(get_ask_limiter),
):
    """Route a prompt to Local, Global, or External context and return an answer."""
    doc, current_chunk_id = await _resolve_ask_target(db, paper_id, payload)

    result = await handle_ask(
        db,
        prompt=payload.query,
        document_id=paper_id,
        current_chunk_id=current_chunk_id,
        conversation_id=payload.conversation_id,
        visible_sequence_orders=payload.visible_sequence_orders,
        focused_element=payload.focused_element,
        user_images_b64=payload.images_b64,
        parent_turn_id=payload.parent_turn_id,
        thread_root_turn_id=payload.thread_root_turn_id,
        document=doc,
    )

    return {
        "answer": result.answer,
        "context_type": result.context_type,
        "router_reason": result.router_reason,
        "citations": [c.model_dump(mode="json") for c in result.citations],
        "model": result.model,
        "conversation_id": str(result.conversation_id) if result.conversation_id else None,
    }


@router.post("/{paper_id}/ask/stream")
async def ask_paper_stream(
    paper_id: UUID,
    payload: AskPayload,
    db: AsyncSession = Depends(get_db),
):
    """Streaming /ask: same pipeline, answer delivered as Server-Sent Events.

    Event types (one JSON object per `data:` line): token, status, replace,
    done (full AskResponse payload), error. The concurrency semaphore is
    acquired inside the generator — a Depends-based limiter would release
    before the stream body even starts running.
    """
    doc, current_chunk_id = await _resolve_ask_target(db, paper_id, payload)

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    async def event_stream():
        async with get_ask_semaphore():
            try:
                async for event in handle_ask_stream(
                    prompt=payload.query,
                    document_id=paper_id,
                    current_chunk_id=current_chunk_id,
                    conversation_id=payload.conversation_id,
                    visible_sequence_orders=payload.visible_sequence_orders,
                    focused_element=payload.focused_element,
                    user_images_b64=payload.images_b64,
                    parent_turn_id=payload.parent_turn_id,
                    thread_root_turn_id=payload.thread_root_turn_id,
                    document=doc,
                ):
                    yield sse(event)
            except NoLLMConfigured as e:
                # The message already carries full instructions — no prefix.
                yield sse({"type": "error", "detail": str(e.model)})
            except ModelUnavailable as e:
                yield sse({"type": "error", "detail": f"Model unavailable: {e.model}"})
            except Exception:
                logger.exception("ask_stream failed")
                yield sse({"type": "error", "detail": "Answer generation failed. Check the server logs."})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tell nginx-style proxies not to buffer the stream.
            "X-Accel-Buffering": "no",
        },
    )


def _serialize_turn(t: dict) -> dict:
    return {
        "id": str(t["id"]),
        "conversation_id": str(t["conversation_id"]) if t.get("conversation_id") else None,
        "role": t["role"],
        "content": t["content"],
        "context_type": t.get("context_type"),
        "citations": t.get("citations"),
        "created_at": t["created_at"].isoformat() if t.get("created_at") else None,
        # Sub-thread support (None for main linear chat turns)
        "parent_turn_id": str(t["parent_turn_id"]) if t.get("parent_turn_id") else None,
    }


@router.get("/{paper_id}/chat")
async def get_paper_chat(
    paper_id: UUID,
    conversation_id: Optional[UUID] = Query(default=None),
    # NEW: when provided, return the full subtree for this sub-thread root
    # (includes the original branching user turn + its first AI reply + all descendants).
    thread_root_turn_id: Optional[UUID] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return saved chat turns for a paper, oldest first.

    - If ``thread_root_turn_id`` is provided → return the full sub-thread subtree
      (special loader that includes the original first AI reply even if its
      parent_turn_id is still NULL).
    - Else if ``conversation_id`` is provided → the main linear chat for that conv
      (turns with parent_turn_id IS NULL), with assistant turns that start a
      sub-thread augmented with `thread_root_turn_id` so the frontend knows
      exactly which user turn to use as the root when the user clicks "Thread →".
    - Otherwise legacy behaviour (all turns for the paper).
    """
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    if thread_root_turn_id is not None:
        turns = await conv_repo.get_thread_subtree(db, thread_root_turn_id)
        # Depth of this sub-thread = chain length of its root turn + 1.
        root_chain = await conv_repo.compute_turn_depth(db, thread_root_turn_id)
        sub_depth = root_chain + 1
        # Pair user→assistant inside the sub-thread so each pair becomes a
        # potential deeper sub-thread root. Suppress pairing for the root
        # itself (clicking it would re-open the same sub-thread).
        serialized = []
        prev_user_id = None
        root_id_str = str(thread_root_turn_id)
        for t in turns:
            st = _serialize_turn(t)
            if t.get("role") == "user":
                prev_user_id = t["id"]
            elif t.get("role") == "assistant" and prev_user_id is not None:
                pid_str = str(prev_user_id)
                # Skip the (root user, first AI reply) pair — same sub-thread.
                if pid_str != root_id_str:
                    st["thread_root_turn_id"] = pid_str
            serialized.append(st)
        return {
            "turns": serialized,
            "is_sub_thread": True,
            "depth": sub_depth,
            "max_depth": MAX_SUB_THREAD_DEPTH,
        }

    if conversation_id is not None:
        # Main chat view must exclude sub-thread turns (parent_turn_id IS NOT NULL),
        # otherwise replies sent inside a tangent leak into the primary linear chat.
        turns = await conv_repo.get_main_chat(db, conversation_id)
    else:
        turns = await conv_repo.list_turns_by_document(db, paper_id)

    # Post-process for the main chat view: attach the preceding user turn id
    # to every assistant turn. The frontend uses this as `thread_root_turn_id`
    # when the user clicks "Thread →". It can separately call has_children / count
    # (or we can extend the response later) to decide whether to actually show
    # the affordance. This keeps the endpoint simple and fast.
    serialized = []
    prev_user_id = None
    for t in turns:
        st = _serialize_turn(t)
        if t.get("role") == "user":
            prev_user_id = t["id"]
        elif t.get("role") == "assistant" and prev_user_id is not None:
            st["thread_root_turn_id"] = str(prev_user_id)
        serialized.append(st)

    return {
        "turns": serialized,
        "is_sub_thread": False,
        "depth": 0,
        "max_depth": MAX_SUB_THREAD_DEPTH,
    }


@router.get("/{paper_id}/conversations")
async def list_paper_conversations(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """List every distinct conversation thread for a paper, most-recent first."""
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    rows = await conv_repo.list_conversations_by_document(db, paper_id)
    return {
        "conversations": [
            {
                "conversation_id": str(r["conversation_id"]) if r.get("conversation_id") else None,
                "turn_count": int(r["turn_count"]),
                "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
                "last_at": r["last_at"].isoformat() if r.get("last_at") else None,
                "first_user_message": r.get("first_user_message"),
            }
            for r in rows
            if r.get("conversation_id") is not None
        ],
    }

