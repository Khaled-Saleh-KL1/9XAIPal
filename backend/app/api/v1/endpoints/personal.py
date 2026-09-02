"""Personal reading state: bookmarks, the reader's own notes, and decks.

This is everything in the reader that belongs to the person rather than to the
document. It used to live in localStorage, which made it per-browser: marks
made on the desktop were invisible when the same paper was opened from the LAN
server on a tablet, and clearing site data destroyed them with no warning.
"""

from uuid import UUID
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.api.errors import DocumentNotFound
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import personal as personal_repo
from app.services import documents as doc_service

router = APIRouter()


# ── Wire formats ────────────────────────────────────────────────────────────


class BookmarkRequest(BaseModel):
    sequence_id: int
    snippet: Optional[str] = None
    kind: str = "block"
    page: Optional[int] = None
    progress: float = 0.0
    label: Optional[str] = None


class BookmarkPatch(BaseModel):
    label: Optional[str] = None


class PersonalNoteRequest(BaseModel):
    anchor_sequence_id: int
    body: str = Field(min_length=1)
    anchor_quote: Optional[str] = None
    margin_side: Optional[str] = None


class PersonalNotePatch(BaseModel):
    body: Optional[str] = Field(default=None, min_length=1)
    margin_side: Optional[str] = None


class DeckMemberModel(BaseModel):
    kind: Literal["ai", "personal"]
    id: UUID


class DeckModel(BaseModel):
    id: UUID
    members: list[DeckMemberModel]
    top: int = 0
    margin_side: str = "right"
    label: Optional[str] = None
    study: bool = False


class DecksRequest(BaseModel):
    decks: list[DeckModel]


# ── Serialization ───────────────────────────────────────────────────────────


def _bookmark(b: dict) -> dict:
    return {
        "id": str(b["id"]),
        "sequence_id": b["sequence_id"],
        "snippet": b.get("snippet"),
        "kind": b.get("kind") or "block",
        "page": b.get("page"),
        "progress": float(b.get("progress") or 0),
        "label": b.get("label"),
        "updated_at": b["updated_at"].isoformat() if b.get("updated_at") else None,
    }


def _personal_note(n: dict) -> dict:
    return {
        "id": str(n["id"]),
        "anchor_sequence_id": n["anchor_sequence_id"],
        "anchor_chunk_id": str(n["anchor_chunk_id"]) if n.get("anchor_chunk_id") else None,
        "anchor_quote": n.get("anchor_quote"),
        "body": n["body"],
        "margin_side": n.get("margin_side") or "right",
        "created_at": n["created_at"].isoformat() if n.get("created_at") else None,
        "updated_at": n["updated_at"].isoformat() if n.get("updated_at") else None,
    }


def _deck(d: dict) -> dict:
    return {
        "id": str(d["id"]),
        "label": d.get("label"),
        "top": d.get("top_index") or 0,
        "margin_side": d.get("margin_side") or "right",
        "study": bool(d.get("study")),
        "members": [
            {"kind": m["kind"], "id": str(m["id"])} for m in d.get("members") or []
        ],
    }


async def _require_document(db: AsyncSession, paper_id: UUID, user_id: UUID) -> dict:
    doc = await doc_service.get_document(db, paper_id, user_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))
    return doc


# ── Everything at once ──────────────────────────────────────────────────────


@router.get("/{paper_id}/personal")
async def get_personal_state(
    paper_id: UUID, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bookmarks, personal notes and decks in one request.

    Fetched together rather than from three endpoints because decks reference
    the other two: served separately, a deck list can arrive describing notes
    a concurrent delete has already removed, and the reader would render a
    stack with a hole in it.
    """
    await _require_document(db, paper_id, current_user["id"])

    # Membership rows vanish with their notes, so a deck can fall below two
    # cards without anyone touching the deck itself. Settle that before
    # reporting, not after the client has drawn it.
    pruned = await personal_repo.prune_thin_decks(db, paper_id)
    if pruned:
        await db.commit()

    return {
        "bookmarks": [_bookmark(b) for b in await personal_repo.list_bookmarks(db, paper_id)],
        "notes": [
            _personal_note(n) for n in await personal_repo.list_personal_notes(db, paper_id)
        ],
        "decks": [_deck(d) for d in await personal_repo.list_decks(db, paper_id)],
    }


# ── Bookmarks ───────────────────────────────────────────────────────────────


@router.get("/{paper_id}/bookmarks")
async def list_bookmarks(
    paper_id: UUID, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _require_document(db, paper_id, current_user["id"])
    rows = await personal_repo.list_bookmarks(db, paper_id)
    return {"bookmarks": [_bookmark(b) for b in rows]}


@router.post("/{paper_id}/bookmarks")
async def create_bookmark(
    paper_id: UUID, payload: BookmarkRequest, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mark a block, or refresh the mark already on it."""
    await _require_document(db, paper_id, current_user["id"])
    row = await personal_repo.upsert_bookmark(
        db,
        document_id=paper_id,
        sequence_id=payload.sequence_id,
        snippet=payload.snippet,
        kind=payload.kind,
        page=payload.page,
        progress=payload.progress,
        label=payload.label,
    )
    await db.commit()
    return _bookmark(row)


@router.patch("/{paper_id}/bookmarks/{bookmark_id}")
async def rename_bookmark(
    paper_id: UUID,
    bookmark_id: UUID,
    payload: BookmarkPatch,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # ⚠ Was missing entirely before the auth retrofit: this checked the
    # bookmark belongs to `paper_id` from the URL, but never verified
    # `paper_id` itself is owned by the caller — a guessed/foreign paper_id
    # paired with its own real bookmark_id would have passed.
    await _require_document(db, paper_id, current_user["id"])
    existing = await personal_repo.get_bookmark(db, bookmark_id)
    if not existing or existing["document_id"] != paper_id:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    row = await personal_repo.set_bookmark_label(db, bookmark_id, payload.label)
    # The UPDATE can still match nothing: the existence check above and this
    # write are two statements, and a concurrent DELETE between them leaves
    # `row` None. Serializing that raises TypeError and answers 500 to what
    # is really a 404 — the bookmark genuinely is gone by the time we wrote.
    if not row:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    await db.commit()
    return _bookmark(row)


@router.delete("/{paper_id}/bookmarks/{bookmark_id}", status_code=204)
async def delete_bookmark(
    paper_id: UUID, bookmark_id: UUID, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _require_document(db, paper_id, current_user["id"])
    existing = await personal_repo.get_bookmark(db, bookmark_id)
    if not existing or existing["document_id"] != paper_id:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    await personal_repo.delete_bookmark(db, bookmark_id)
    await db.commit()


# ── Personal notes ──────────────────────────────────────────────────────────


@router.get("/{paper_id}/personal-notes")
async def list_personal_notes(
    paper_id: UUID, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _require_document(db, paper_id, current_user["id"])
    rows = await personal_repo.list_personal_notes(db, paper_id)
    return {"notes": [_personal_note(n) for n in rows]}


@router.post("/{paper_id}/personal-notes")
async def create_personal_note(
    paper_id: UUID, payload: PersonalNoteRequest, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _require_document(db, paper_id, current_user["id"])

    # ⚠ Resolve the chunk from the sequence id rather than accepting one from
    # the client — same rule as paper notes. Re-chunking recreates every row
    # with fresh UUIDs, so a tab opened beforehand holds ids that no longer
    # exist and inserting one violates the foreign key.
    chunk = await chunk_repo.get_chunk_by_sequence(db, paper_id, payload.anchor_sequence_id)

    row = await personal_repo.create_personal_note(
        db,
        document_id=paper_id,
        anchor_sequence_id=payload.anchor_sequence_id,
        anchor_chunk_id=chunk["id"] if chunk else None,
        anchor_quote=payload.anchor_quote,
        body=payload.body,
        margin_side=payload.margin_side or "right",
    )
    await db.commit()
    return _personal_note(row)


@router.patch("/{paper_id}/personal-notes/{note_id}")
async def update_personal_note(
    paper_id: UUID,
    note_id: UUID,
    payload: PersonalNotePatch,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # Same gap as rename_bookmark — the paper itself must be verified owned,
    # not just that the note points at this paper_id.
    await _require_document(db, paper_id, current_user["id"])
    existing = await personal_repo.get_personal_note(db, note_id)
    if not existing or existing["document_id"] != paper_id:
        raise HTTPException(status_code=404, detail="Note not found")
    row = await personal_repo.update_personal_note(
        db, note_id, body=payload.body, margin_side=payload.margin_side
    )
    # Same two-statement race as rename_bookmark above.
    if not row:
        raise HTTPException(status_code=404, detail="Note not found")
    await db.commit()
    return _personal_note(row)


@router.delete("/{paper_id}/personal-notes/{note_id}", status_code=204)
async def delete_personal_note(
    paper_id: UUID, note_id: UUID, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _require_document(db, paper_id, current_user["id"])
    existing = await personal_repo.get_personal_note(db, note_id)
    if not existing or existing["document_id"] != paper_id:
        raise HTTPException(status_code=404, detail="Note not found")
    await personal_repo.delete_personal_note(db, note_id)
    # Taking a card out can leave its deck holding one, which is not a deck.
    await personal_repo.prune_thin_decks(db, paper_id)
    await db.commit()


# ── Decks ───────────────────────────────────────────────────────────────────


@router.get("/{paper_id}/decks")
async def list_decks(
    paper_id: UUID, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _require_document(db, paper_id, current_user["id"])
    pruned = await personal_repo.prune_thin_decks(db, paper_id)
    if pruned:
        await db.commit()
    return {"decks": [_deck(d) for d in await personal_repo.list_decks(db, paper_id)]}


@router.put("/{paper_id}/decks")
async def replace_decks(
    paper_id: UUID, payload: DecksRequest, db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Replace this paper's whole deck arrangement.

    A whole-collection PUT rather than per-deck CRUD: one drag can dissolve a
    deck, create another, and move a card between two more. That is a single
    arrangement, computed in one place on the client and applied in one
    transaction here. Split into granular calls it becomes an ordered sequence
    with a half-applied state between every pair, and a request dropped in the
    middle leaves a card in two decks or in none.
    """
    await _require_document(db, paper_id, current_user["id"])

    # Only ownership is checked here: a member has to be a note on *this*
    # paper, which needs the database. Deduplicating members and dropping
    # decks that fall below two cards is the repository's job, so that the
    # rule holds for every caller rather than only this one.
    valid_ai, valid_personal = await personal_repo.owned_note_ids(db, paper_id)

    cleaned: list[dict[str, Any]] = [
        {
            "id": deck.id,
            "label": deck.label,
            "top_index": deck.top,
            "margin_side": deck.margin_side,
            "study": deck.study,
            "members": [
                {"kind": m.kind, "id": m.id}
                for m in deck.members
                if m.id in (valid_ai if m.kind == "ai" else valid_personal)
            ],
        }
        for deck in payload.decks
    ]

    await personal_repo.replace_decks(db, paper_id, cleaned)
    await db.commit()
    return {"decks": [_deck(d) for d in await personal_repo.list_decks(db, paper_id)]}
