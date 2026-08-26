"""Study endpoints: paper groups, and the chat scoped to one.

A study is the unit of "what may this question be answered from". Its chat is a
rolling transcript in ``conversation_turns`` — the same artifact the book chat
stores — rather than a note, because a desk conversation has follow-ups and
pronouns that need the turns before them.

⚠ **The literal path segment ``library`` is a valid study id.** It means the
library-wide scope: every paper the CALLER owns, no group. Routing it as a
scope rather than 404ing keeps one set of endpoints for both, and
`study_id IS NULL` on the turn rows says the same thing in the database.

⚠ **`_resolve_scope`'s LIBRARY branch is the one place "library-wide" used to
mean "every document in the database", not "every document this user owns".**
That was a real cross-tenant leak once a second account existed: the desk's
"ask the whole library" chat would cite, quote, and answer from every other
user's private papers. It is fixed here by requiring `user_id` and filtering
`list_documents` by it — see the call below.
"""

import json
from uuid import UUID, uuid4
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_ask_semaphore, get_current_user
from app.api.errors import ModelUnavailable, NoLLMConfigured
from app.chat.study_agent import answer_study_question
from app.core.config import settings
from app.core.logging import get_logger
from app.database.connection import async_session_factory
from app.database.repositories import documents as doc_repo
from app.database.repositories import stickies as sticky_repo
from app.database.repositories import studies as study_repo
from app.llm.catalog import resolve_requested_model
from app.services import documents as doc_service

logger = get_logger(__name__)
router = APIRouter()

# The path segment that means "every paper", as opposed to a study's UUID.
LIBRARY = "library"


class StudyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = None


class StudyPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = None


class StudyPapersRequest(BaseModel):
    document_ids: list[UUID]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    model: Optional[str] = None


def _display_title(doc: dict) -> str:
    """The same resolution the library uses: a rename wins over the filename."""
    return (doc.get("title") or "").strip() or (
        doc.get("original_filename") or ""
    ).rsplit(".", 1)[0]


def _paper(doc: dict, index: Optional[int] = None) -> dict:
    out = {
        "id": str(doc["id"]),
        "title": _display_title(doc),
        "page_count": doc.get("page_count"),
        "status": doc.get("status"),
    }
    if index is not None:
        out["paper"] = index  # the P<n> the agent cites this paper by
    return out


def _study(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row.get("description"),
        "paper_count": int(row.get("paper_count") or 0),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def _turn(row: dict) -> dict:
    citations = row.get("citations")
    if isinstance(citations, str):
        try:
            citations = json.loads(citations)
        except ValueError:
            citations = []
    steps = row.get("agent_steps")
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError:
            steps = []
    return {
        "id": str(row["id"]),
        "role": row["role"],
        "content": row.get("content") or "",
        "model": row.get("model"),
        "cited": citations or [],
        "agent_steps": steps or [],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


async def _resolve_scope(
    db: AsyncSession, study_id: str, user_id: UUID
) -> tuple[Optional[UUID], list[dict]]:
    """Turn a path segment into (study_id | None, papers in citation order).

    ⚠ The library scope's paper order is the library's own (newest first), not
    a stored position. That is deliberate: there is no membership row to hold a
    position, and a citation into the library scope is only ever read inside
    the answer that produced it.
    """
    if study_id == LIBRARY:
        docs = await doc_service.list_documents(db, user_id, limit=settings.study_max_papers, offset=0)
        return None, [d for d in docs if d.get("status") == "complete"]
    try:
        sid = UUID(study_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="No such study")
    study = await study_repo.get_study(db, sid, user_id)
    if not study:
        raise HTTPException(status_code=404, detail="No such study")
    return sid, await study_repo.list_study_papers(db, sid)


# ── Studies ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_studies(db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    rows = await study_repo.list_studies(db, current_user["id"])
    return {"studies": [_study(r) for r in rows]}


@router.post("", status_code=201)
async def create_study(
    payload: StudyRequest, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = await study_repo.create_study(
        db, user_id=current_user["id"], name=payload.name.strip(), description=payload.description
    )
    await db.commit()
    return _study({**row, "paper_count": 0})


@router.get("/{study_id}")
async def get_study(
    study_id: str, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """A study and its papers, in citation order.

    Answers for ``library`` too, so the client can render either scope from one
    call. The synthetic library study has no id and cannot be renamed.
    """
    sid, papers = await _resolve_scope(db, study_id, current_user["id"])
    if sid is None:
        return {
            "study": {
                "id": LIBRARY,
                "name": "Whole library",
                "description": "Every finished paper. Not a group — the default scope.",
                "paper_count": len(papers),
                "created_at": None,
                "updated_at": None,
            },
            "papers": [_paper(d, i + 1) for i, d in enumerate(papers)],
        }
    row = await study_repo.get_study(db, sid, current_user["id"])
    return {
        "study": _study({**row, "paper_count": len(papers)}),
        "papers": [_paper(d, i + 1) for i, d in enumerate(papers)],
    }


@router.patch("/{study_id}")
async def rename_study(
    study_id: UUID, payload: StudyPatch, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    name = payload.name.strip() if payload.name is not None else None
    if name == "":
        raise HTTPException(status_code=400, detail="A study needs a name")
    row = await study_repo.update_study(
        db, study_id, current_user["id"], name=name, description=payload.description
    )
    if not row:
        raise HTTPException(status_code=404, detail="No such study")
    await db.commit()
    papers = await study_repo.list_study_papers(db, study_id)
    return _study({**row, "paper_count": len(papers)})


@router.delete("/{study_id}", status_code=204)
async def delete_study(
    study_id: UUID, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Delete a study and its chat. The papers themselves are untouched."""
    if not await study_repo.delete_study(db, study_id, current_user["id"]):
        raise HTTPException(status_code=404, detail="No such study")
    await db.commit()


@router.put("/{study_id}/papers")
async def set_study_papers(
    study_id: UUID, payload: StudyPapersRequest, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Replace the study's membership wholesale; list order sets citation order.

    ⚠ Whole-collection, like note decks. Add/remove calls would make the client
    reconcile ordering across two round trips, and a dropped request would
    leave a study whose P-numbers no longer match the answers already on screen.
    """
    if not await study_repo.get_study(db, study_id, current_user["id"]):
        raise HTTPException(status_code=404, detail="No such study")
    if len(payload.document_ids) > settings.study_max_papers:
        raise HTTPException(
            status_code=400,
            detail=(
                f"A study holds at most {settings.study_max_papers} papers. "
                "Every paper is loaded on every question, so a larger scope "
                "costs more than it answers."
            ),
        )
    # Narrow to documents this user actually owns — otherwise a study could be
    # made to include another user's paper, and its content would then leak
    # through this study's own (correctly user-scoped) chat.
    owned_ids = await doc_repo.filter_owned_document_ids(db, payload.document_ids, current_user["id"])
    papers = await study_repo.set_study_papers(db, study_id, owned_ids)
    await db.commit()
    return {"papers": [_paper(d, i + 1) for i, d in enumerate(papers)]}


# ── The chat ────────────────────────────────────────────────────────────────

@router.get("/{study_id}/chat")
async def get_chat(
    study_id: str, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    sid, _ = await _resolve_scope(db, study_id, current_user["id"])
    rows = await study_repo.list_turns(db, current_user["id"], sid)
    return {"turns": [_turn(r) for r in rows]}


@router.delete("/{study_id}/chat", status_code=204)
async def clear_chat(
    study_id: str, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    sid, _ = await _resolve_scope(db, study_id, current_user["id"])
    await study_repo.clear_turns(db, current_user["id"], sid)
    await db.commit()


@router.post("/{study_id}/chat/stream")
async def chat_stream(
    study_id: str, payload: ChatRequest, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Ask the study a question and stream the answer as Server-Sent Events.

    Events: ``created`` (the user turn is stored), ``status``, ``step``,
    ``token``, ``done``, ``error`` — the same shapes the note stream emits, so
    one client component renders both.

    ⚠ Opens its own session for the generator body. FastAPI tears down
    ``Depends`` sessions before a StreamingResponse body runs, so the request
    session is already closed by the time the first token arrives.
    """
    user_id = current_user["id"]
    sid, papers = await _resolve_scope(db, study_id, user_id)
    requested_model = resolve_requested_model(payload.model)

    conversation_id = await study_repo.latest_conversation_id(db, user_id, sid) or uuid4()
    history = await study_repo.list_turns(db, user_id, sid)

    user_turn = await study_repo.add_turn(
        db,
        user_id=user_id,
        study_id=sid,
        conversation_id=conversation_id,
        role="user",
        content=payload.question,
    )
    await db.commit()

    # What is already pinned, so the agent can build on it and will not re-pin
    # what it wrote last turn. Read here, while the request session is open.
    chat_notes = await sticky_repo.list_stickies(db, user_id=user_id, board="chat", study_id=sid)
    universal_notes = await sticky_repo.list_stickies(db, user_id=user_id, board="universal")

    # Plain dicts, read while the request session is still open.
    papers = [dict(p) for p in papers]
    history = [{"role": t["role"], "content": t["content"]} for t in history]
    chat_notes = [{"body": n["body"], "origin": n["origin"]} for n in chat_notes]
    universal_notes = [{"body": n["body"], "origin": n["origin"]} for n in universal_notes]

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    async def event_stream():
        yield sse({"type": "created", "turn_id": str(user_turn["id"])})
        async with get_ask_semaphore():
            answer, model, cited, steps = "", "", [], []
            try:
                async with async_session_factory() as session:
                    async for event in answer_study_question(
                        session,
                        user_id=user_id,
                        papers=papers,
                        question=payload.question,
                        history=history,
                        model=requested_model,
                        study_id=sid,
                        chat_notes=chat_notes,
                        universal_notes=universal_notes,
                    ):
                        if event["type"] == "done":
                            answer = event.get("answer") or ""
                            model = event.get("model") or ""
                            cited = event.get("cited") or []
                            steps = event.get("steps") or []
                        else:
                            yield sse(event)

                    turn = await study_repo.add_turn(
                        session,
                        user_id=user_id,
                        study_id=sid,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                        model=model,
                        citations=cited,
                        agent_steps=steps,
                    )
                    await session.commit()

                yield sse({
                    "type": "done",
                    "turn_id": str(turn["id"]),
                    "answer": answer,
                    "model": model,
                    "cited": cited,
                    "agent_steps": steps,
                })
            except NoLLMConfigured as e:
                yield sse({"type": "error", "detail": str(e.model)})
            except ModelUnavailable as e:
                yield sse({"type": "error", "detail": f"Model unavailable: {e.model}"})
            except Exception:
                logger.exception("study chat failed for scope %s", study_id)
                yield sse({
                    "type": "error",
                    "detail": "Could not answer that. Check the server logs.",
                })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
