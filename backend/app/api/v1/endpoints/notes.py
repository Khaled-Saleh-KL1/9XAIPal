"""Note endpoints: the anchored margin annotations that replaced the chat pane.

A note is created, answered, and persisted in one streaming request. There is
no router, no guardrail, no conversation compaction on this path — see
app.chat.paper_agent for why none of that applies.
"""

import json
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_ask_semaphore, get_current_user
from app.api.errors import DocumentNotFound, ModelUnavailable, NoLLMConfigured
from app.chat.paper_agent import answer_paper_question
from app.core.config import settings
from app.core.logging import get_logger
from app.database.connection import async_session_factory
from app.database.repositories import assets as asset_repo
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import notes as note_repo
from app.llm.catalog import resolve_requested_model
from app.services import documents as doc_service

logger = get_logger(__name__)

router = APIRouter()


class NoteAnchor(BaseModel):
    """Where in the paper a note hangs."""

    # 'text' (a highlighted passage), 'figure'/'equation'/'table' (a picture,
    # a formula, a whole table), 'block' (the reader asked without selecting
    # anything, so we anchor to what's in view), or 'document' (the holistic
    # level — a question about the paper as a whole, asked from the panel).
    kind: str = "text"
    sequence_id: int
    chunk_id: Optional[UUID] = None
    quote: Optional[str] = None
    # As served to the browser, e.g. "/static/images/<doc>/<uuid>.png". Stripped
    # back to a storage-relative path before it reaches the model.
    image_url: Optional[str] = None


class NoteRequest(BaseModel):
    question: str = Field(min_length=1)
    anchor: NoteAnchor
    # Set when continuing an existing note rather than starting a new one.
    parent_note_id: Optional[UUID] = None
    # 'left' | 'right' | None. None lets the server balance the margins.
    margin_side: Optional[str] = None
    # Which model to ask. None uses the configured default.
    # ⚠ Ignored for follow-ups — see the note in create_note_stream.
    model: Optional[str] = None


class MoveNoteRequest(BaseModel):
    margin_side: str


# A note is placed opposite the crowd: if the margin that would normally take
# it already holds notes anchored within this many blocks, the other margin
# gets it instead. Wide enough to catch cards that would actually collide on
# screen, narrow enough that distant notes do not push each other around.
_CROWDING_WINDOW = 6


async def _choose_margin(
    db: AsyncSession, document_id: UUID, anchor_sequence_id: int
) -> str:
    """Pick the less crowded margin for a new note at this anchor.

    Cards stack downward when they collide, so putting every note on one side
    pushes later ones far from the text they describe. Alternating by local
    density keeps each card beside its own paragraph.
    """
    rows = await note_repo.list_notes(db, document_id)
    near = [
        n for n in rows
        if n.get("parent_note_id") is None
        # ⚠ Document-scope notes are excluded, not merely irrelevant. They all
        # carry the first block's sequence id to satisfy a NOT NULL column, so
        # counting them would make every note near the top of the paper look
        # crowded on whichever side they nominally landed on.
        and (n.get("scope") or "anchor") == "anchor"
        and abs((n.get("anchor_sequence_id") or 0) - anchor_sequence_id) <= _CROWDING_WINDOW
    ]
    right = sum(1 for n in near if (n.get("margin_side") or "right") == "right")
    left = len(near) - right
    return "left" if right > left else "right"


_IMAGE_URL_PREFIX = "/static/images/"


def _to_storage_path(image_url: Optional[str]) -> Optional[str]:
    """Turn a served image URL back into a chunk_assets.file_path.

    ⚠ Only ever accepts the shape this app's own /static/images/ links use.
    Anything else — an absolute path, a bare filename, a `../` escape — is
    rejected here rather than passed through, because this string reaches
    disk (see build_multimodal_messages) if it survives. This is layer one
    of two: the caller separately verifies the result names a real,
    owned chunk_assets row (asset_repo.file_path_belongs_to_document)
    before it's ever used — this function alone is necessary but not
    sufficient, since a forged path can still be shaped like a real one.
    """
    if not image_url or not image_url.startswith(_IMAGE_URL_PREFIX):
        return None
    return image_url[len(_IMAGE_URL_PREFIX):]


def _serialize_note(n: dict) -> dict:
    return {
        "id": str(n["id"]),
        "scope": n.get("scope") or "anchor",
        "agent_steps": n.get("agent_steps") or [],
        "anchor_sequence_id": n["anchor_sequence_id"],
        "anchor_chunk_id": str(n["anchor_chunk_id"]) if n.get("anchor_chunk_id") else None,
        "anchor_kind": n.get("anchor_kind") or "text",
        "anchor_quote": n.get("anchor_quote"),
        "anchor_image_path": n.get("anchor_image_path"),
        "question": n["question"],
        "answer": n.get("answer") or "",
        "cited_sequence_ids": list(n.get("cited_sequence_ids") or []),
        "retrieval_mode": n.get("retrieval_mode"),
        "model": n.get("model"),
        "margin_side": n.get("margin_side") or "right",
        "requested_model": n.get("requested_model"),
        "parent_note_id": str(n["parent_note_id"]) if n.get("parent_note_id") else None,
        "created_at": n["created_at"].isoformat() if n.get("created_at") else None,
    }


@router.get("/{paper_id}/notes")
async def list_notes(
    paper_id: UUID, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    """Every note on this paper, ordered by anchor position."""
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))
    rows = await note_repo.list_notes(db, paper_id)
    return {"notes": [_serialize_note(n) for n in rows]}


@router.delete("/{paper_id}/notes/{note_id}", status_code=204)
async def delete_note(
    paper_id: UUID, note_id: UUID, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Delete a note. Its follow-ups cascade with it."""
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))
    note = await note_repo.get_note(db, note_id)
    if not note or note["document_id"] != paper_id:
        raise HTTPException(status_code=404, detail="Note not found")
    await note_repo.delete_note(db, note_id)
    await db.commit()


@router.patch("/{paper_id}/notes/{note_id}/margin")
async def move_note(
    paper_id: UUID,
    note_id: UUID,
    payload: MoveNoteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Move a note to the other margin."""
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))
    note = await note_repo.get_note(db, note_id)
    if not note or note["document_id"] != paper_id:
        raise HTTPException(status_code=404, detail="Note not found")
    if payload.margin_side not in ("left", "right"):
        raise HTTPException(status_code=400, detail="margin_side must be 'left' or 'right'")
    await note_repo.set_margin_side(db, note_id, payload.margin_side)
    await db.commit()
    return {"id": str(note_id), "margin_side": payload.margin_side}


@router.post("/{paper_id}/notes/stream")
async def create_note_stream(
    paper_id: UUID,
    payload: NoteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a note and stream its answer as Server-Sent Events.

    Events: ``created`` (carries the note id so the card can render at once),
    ``status``, ``step``, ``token``, ``done``, ``error``.

    ``step`` arrives twice per tool call — once as ``running`` when the agent
    announces it and once as ``done`` with what came back — keyed by ``id`` so
    the client updates the row in place rather than appending a duplicate.

    The row is inserted before generation so the question is never lost to a
    failed model call — a note with an empty answer is a visible, retryable
    state, whereas a dropped request is not.

    ⚠ Opens its own session for the generator body. FastAPI tears down
    ``Depends`` sessions before a StreamingResponse body runs, so the request
    session is already closed by the time the first token arrives.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    anchor = payload.anchor

    # ⚠ Resolve the chunk id from the sequence id, ALWAYS — never trust the one
    # the client sent. Re-chunking deletes and recreates every row with fresh
    # UUIDs, so any tab opened before a re-chunk holds ids that no longer
    # exist, and inserting one violates the foreign key and 500s the request.
    # sequence_id is the durable anchor; chunk_id is only a convenience.
    chunk = await chunk_repo.get_chunk_by_sequence(db, paper_id, anchor.sequence_id)
    anchor_chunk_id = chunk["id"] if chunk else None

    image_path = _to_storage_path(anchor.image_url)
    if image_path and not await asset_repo.file_path_belongs_to_document(db, image_path, paper_id):
        # A forged or stale reference (an old tab open across a re-chunk, or
        # a client sending a path that was never this paper's) — dropped the
        # same way a chunk_id that no longer resolves is, not treated as an
        # error. See _to_storage_path and file_path_belongs_to_document for
        # why this check exists at all: it is the only thing standing
        # between this client-supplied string and an arbitrary file read.
        image_path = None

    # A follow-up belongs to its parent's card, so it inherits that side and —
    # more importantly — that model.
    #
    # ⚠ The inherited model is taken from the stored note, NOT from the
    # request. A thread that switched models halfway would silently invalidate
    # the comparison the picker exists to support: you would no longer know
    # which model said what. The client cannot override this.
    #
    # ⚠ The parent must belong to THIS paper. get_note looks a note up by id
    # alone, so without this check any note id the client sends is accepted —
    # and the whole parent thread is read below and handed to the model as
    # context (see `thread` and answer_paper_question). That is someone
    # else's questions and answers, about someone else's document, inside
    # this answer. Same 404 shape as delete_note, which already checks it.
    parent: Optional[dict] = None
    if payload.parent_note_id:
        parent = await note_repo.get_note(db, payload.parent_note_id)
        if not parent or parent["document_id"] != paper_id:
            raise HTTPException(status_code=404, detail="Note not found")

    # ⚠ Scope is derived from the anchor kind, never sent as its own field.
    # A request carrying kind='document' with scope='anchor' (or the reverse)
    # has no coherent meaning — the two say the same thing — and accepting both
    # only creates rows the UI cannot place. A follow-up inherits its parent's.
    scope = "document" if anchor.kind == "document" else "anchor"

    if parent:
        scope = parent.get("scope") or "anchor"
        margin_side = parent.get("margin_side") or "right"
        requested_model = parent.get("requested_model")
    else:
        requested_model = resolve_requested_model(payload.model)
        if scope == "document":
            # Never rendered in a gutter, so there is no side to balance.
            margin_side = "right"
        elif payload.margin_side in ("left", "right"):
            margin_side = payload.margin_side
        else:
            margin_side = await _choose_margin(db, paper_id, anchor.sequence_id)

    note = await note_repo.create_note(
        db,
        document_id=paper_id,
        anchor_sequence_id=anchor.sequence_id,
        anchor_chunk_id=anchor_chunk_id,
        anchor_kind=anchor.kind,
        anchor_quote=anchor.quote,
        anchor_image_path=image_path,
        question=payload.question,
        parent_note_id=payload.parent_note_id,
        margin_side=margin_side,
        requested_model=requested_model,
        scope=scope,
    )
    await db.commit()
    note_id = note["id"]

    # The follow-up chain is read here, while the request session is still
    # open, and passed down as plain dicts.
    thread: list[dict] = []
    if payload.parent_note_id:
        thread = [
            t for t in await note_repo.get_note_thread(db, payload.parent_note_id)
            if t["id"] != note_id
        ]

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    async def event_stream():
        yield sse({"type": "created", "note_id": str(note_id)})
        async with get_ask_semaphore():
            answer = ""
            model = ""
            retrieval_mode = None
            cited: list[int] = []
            steps: list[dict] = []
            try:
                async with async_session_factory() as session:
                    async for event in answer_paper_question(
                        session,
                        document_id=paper_id,
                        user_id=current_user["id"],
                        question=payload.question,
                        anchor={
                            "kind": anchor.kind,
                            "sequence_id": anchor.sequence_id,
                            "quote": anchor.quote,
                        },
                        doc_kind=doc.get("doc_kind") or "paper",
                        thread=thread,
                        image_paths=[image_path] if image_path else None,
                        model=requested_model,
                        # A holistic question has no anchor doing half the
                        # retrieval, so it gets a bigger round budget.
                        max_steps=(
                            settings.paper_agent_holistic_max_steps
                            if scope == "document"
                            else None
                        ),
                    ):
                        if event["type"] == "done":
                            answer = event.get("answer") or ""
                            model = event.get("model") or ""
                            retrieval_mode = event.get("retrieval_mode")
                            cited = event.get("cited") or []
                            steps = event.get("steps") or []
                        else:
                            yield sse(event)

                    await note_repo.finalize_note(
                        session,
                        note_id,
                        answer=answer,
                        model=model,
                        retrieval_mode=retrieval_mode,
                        cited_sequence_ids=cited,
                        agent_steps=steps,
                    )
                    await session.commit()

                yield sse({
                    "type": "done",
                    "note_id": str(note_id),
                    "answer": answer,
                    "model": model,
                    "retrieval_mode": retrieval_mode,
                    "cited_sequence_ids": cited,
                    "agent_steps": steps,
                })
            except NoLLMConfigured as e:
                yield sse({"type": "error", "detail": str(e.model)})
            except ModelUnavailable as e:
                yield sse({"type": "error", "detail": f"Model unavailable: {e.model}"})
            except Exception:
                logger.exception("note generation failed for %s", note_id)
                yield sse({
                    "type": "error",
                    "detail": "Could not generate this note. Check the server logs.",
                })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
