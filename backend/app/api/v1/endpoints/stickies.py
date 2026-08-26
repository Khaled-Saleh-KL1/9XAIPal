"""Sticky-note endpoints: the notes the reader keeps in front of them.

A sticky has no anchor. It may name several papers, one, or none — and none is
a first-class case, not an incomplete row: a note about nothing in particular
shows on every desk.
"""

from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.database.repositories import stickies as sticky_repo

router = APIRouter()


class StickyRequest(BaseModel):
    body: str = ""
    color: Optional[str] = None
    pinned: bool = False
    document_ids: list[UUID] = Field(default_factory=list)


class StickyPatch(BaseModel):
    body: Optional[str] = None
    color: Optional[str] = None
    pinned: Optional[bool] = None
    # ⚠ `None` leaves the scope alone; `[]` clears it, making the note global.
    # Collapsing the two would make "about nothing in particular" unreachable.
    document_ids: Optional[list[UUID]] = None


def _sticky(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "body": row.get("body") or "",
        "color": row.get("color") or "yellow",
        "pinned": bool(row.get("pinned")),
        "papers": [
            {"document_id": str(p["document_id"]), "label": p["label"]}
            for p in row.get("papers") or []
        ],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.get("")
async def list_stickies(
    document_id: list[UUID] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
):
    """Stickies, pinned first then newest.

    Repeat `document_id` to narrow to a scope. The result then holds notes about
    any of those papers **plus every unscoped note** — an unscoped sticky is
    relevant everywhere, so filtering it out of a study's desk would be wrong.
    """
    rows = await sticky_repo.list_stickies(
        db, document_ids=list(document_id) if document_id else None
    )
    return {"stickies": [_sticky(r) for r in rows]}


@router.post("", status_code=201)
async def create_sticky(payload: StickyRequest, db: AsyncSession = Depends(get_db)):
    row = await sticky_repo.create_sticky(
        db,
        body=payload.body,
        color=payload.color,
        pinned=payload.pinned,
        document_ids=payload.document_ids,
    )
    await db.commit()
    return _sticky(row)


@router.patch("/{sticky_id}")
async def update_sticky(
    sticky_id: UUID, payload: StickyPatch, db: AsyncSession = Depends(get_db)
):
    row = await sticky_repo.update_sticky(
        db,
        sticky_id,
        body=payload.body,
        color=payload.color,
        pinned=payload.pinned,
        document_ids=payload.document_ids,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No such sticky note")
    await db.commit()
    return _sticky(row)


@router.delete("/{sticky_id}", status_code=204)
async def delete_sticky(sticky_id: UUID, db: AsyncSession = Depends(get_db)):
    if not await sticky_repo.delete_sticky(db, sticky_id):
        raise HTTPException(status_code=404, detail="No such sticky note")
    await db.commit()
