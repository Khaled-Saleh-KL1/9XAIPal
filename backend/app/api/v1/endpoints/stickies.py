"""Sticky-note endpoints: the two boards.

`board=chat` is the strip beside one conversation, keyed by `scope` (a study id
or the literal `library`). `board=universal` is the standalone board.

⚠ **Deleting is reader-only, and that is a structural fact rather than a check
here.** The assistant creates and edits notes by calling the repository from
`study_agent`; it has no delete tool and does not import `delete_sticky`. This
endpoint is what the UI's × calls.
"""

from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.database.repositories import stickies as sticky_repo

router = APIRouter()

# The scope segment that means the library-wide chat rather than a saved study.
LIBRARY = "library"


def _scope_to_study_id(scope: Optional[str]) -> Optional[UUID]:
    """`library` / absent → None (the library-wide chat); otherwise a study id."""
    if not scope or scope == LIBRARY:
        return None
    try:
        return UUID(scope)
    except ValueError:
        raise HTTPException(status_code=400, detail="scope must be a study id or 'library'")


class StickyRequest(BaseModel):
    body: str = ""
    color: Optional[str] = None
    pinned: bool = False
    board: str = "universal"
    # Study id or 'library'. Ignored when board='universal'.
    scope: Optional[str] = None
    document_ids: list[UUID] = Field(default_factory=list)


class StickyPatch(BaseModel):
    body: Optional[str] = None
    color: Optional[str] = None
    pinned: Optional[bool] = None
    # Supply both to move a note between boards. `board` alone is not enough:
    # scope=None is a real destination (the library chat), not "unset".
    board: Optional[str] = None
    scope: Optional[str] = None
    document_ids: Optional[list[UUID]] = None


def _sticky(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "body": row.get("body") or "",
        "color": row.get("color") or "yellow",
        "pinned": bool(row.get("pinned")),
        "board": row.get("board") or "universal",
        "scope": str(row["study_id"]) if row.get("study_id") else LIBRARY,
        # 'user' or 'assistant'. The badge the UI draws, and the label the
        # agent sees when its own notes are read back to it.
        "origin": row.get("origin") or "user",
        "author_model": row.get("author_model"),
        "papers": [
            {"document_id": str(p["document_id"]), "label": p["label"]}
            for p in row.get("papers") or []
        ],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.get("")
async def list_stickies(
    board: str = Query(default="universal"),
    scope: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """One board's notes, pinned first then newest.

    `?board=universal` — the standalone board.
    `?board=chat&scope=<studyId|library>` — the strip beside that conversation.
    """
    rows = await sticky_repo.list_stickies(
        db,
        board=board,
        study_id=_scope_to_study_id(scope) if board == "chat" else None,
    )
    return {"stickies": [_sticky(r) for r in rows]}


@router.post("", status_code=201)
async def create_sticky(payload: StickyRequest, db: AsyncSession = Depends(get_db)):
    row = await sticky_repo.create_sticky(
        db,
        body=payload.body,
        board=payload.board,
        study_id=_scope_to_study_id(payload.scope) if payload.board == "chat" else None,
        color=payload.color,
        pinned=payload.pinned,
        # Notes made through the API are the reader's. The assistant writes its
        # own by calling the repository directly, so there is no way for a
        # client to forge an assistant note.
        origin="user",
        document_ids=payload.document_ids,
    )
    await db.commit()
    return _sticky(row)


@router.patch("/{sticky_id}")
async def update_sticky(
    sticky_id: UUID, payload: StickyPatch, db: AsyncSession = Depends(get_db)
):
    """Edit a note, or move it between boards.

    ⚠ `origin` cannot be patched. A note the assistant wrote stays marked as
    the assistant's however often the reader edits it: the marker records where
    the claim came from, and letting an edit launder it makes the badge
    worthless.
    """
    move = payload.board is not None
    row = await sticky_repo.update_sticky(
        db,
        sticky_id,
        body=payload.body,
        color=payload.color,
        pinned=payload.pinned,
        board=payload.board,
        study_id=_scope_to_study_id(payload.scope) if move else None,
        move_scope=move,
        document_ids=payload.document_ids,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No such sticky note")
    await db.commit()
    return _sticky(row)


@router.delete("/{sticky_id}", status_code=204)
async def delete_sticky(sticky_id: UUID, db: AsyncSession = Depends(get_db)):
    """Remove a note. **Reader-only** — the assistant has no path to this."""
    if not await sticky_repo.delete_sticky(db, sticky_id):
        raise HTTPException(status_code=404, detail="No such sticky note")
    await db.commit()
