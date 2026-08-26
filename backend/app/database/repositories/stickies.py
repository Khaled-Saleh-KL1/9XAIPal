"""Sticky notes: what the reader wants kept in front of them.

⚠ **Deliberately not `personal_notes`.** A personal note is anchored to a block
in one document and lives in that document's margin; a sticky has no anchor,
may name several papers or none, and lives on the desk. Sharing a table would
give every sticky an `anchor_sequence_id` that means nothing and would surface
stickies in the margin layout.

⚠ **Zero papers is a first-class scope, not an incomplete row.** A note about
nothing in particular — a question to come back to, a definition worth
remembering — shows on every desk. Code that treats an empty `document_ids` as
"not yet assigned" will hide exactly the notes the reader most wanted pinned.
"""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The UI maps these to CSS variables so they survive the light/dark switch;
# storing a hex here would not. Anything else falls back to 'yellow'.
COLORS = ("yellow", "blue", "green", "pink", "plain")


def normalize_color(color: Optional[str]) -> str:
    c = (color or "").strip().lower()
    return c if c in COLORS else "yellow"


async def _attach_papers(session: AsyncSession, rows: list[dict]) -> list[dict]:
    """Fold each sticky's paper ids onto it in one extra query, not N."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    result = await session.execute(
        text("""
            SELECT snp.sticky_id, snp.document_id,
                   COALESCE(NULLIF(TRIM(d.title), ''), d.original_filename) AS label
            FROM sticky_note_papers snp
            JOIN documents d ON d.id = snp.document_id
            WHERE snp.sticky_id = ANY(:ids)
        """),
        {"ids": ids},
    )
    by_sticky: dict = {}
    for r in result.mappings().all():
        by_sticky.setdefault(r["sticky_id"], []).append(
            {"document_id": r["document_id"], "label": r["label"]}
        )
    for row in rows:
        row["papers"] = by_sticky.get(row["id"], [])
    return rows


async def list_stickies(
    session: AsyncSession, *, document_ids: Optional[list[UUID]] = None
) -> list[dict]:
    """Stickies, pinned first then newest.

    ``document_ids`` narrows to notes about any of those papers **plus every
    unscoped note** — an unscoped sticky is relevant everywhere, so filtering
    it out of a study's desk would be wrong. Passing ``None`` returns all.
    """
    if document_ids is None:
        result = await session.execute(
            text("""
                SELECT * FROM sticky_notes
                ORDER BY pinned DESC, updated_at DESC
            """)
        )
    else:
        result = await session.execute(
            text("""
                SELECT sn.* FROM sticky_notes sn
                WHERE NOT EXISTS (
                          SELECT 1 FROM sticky_note_papers p WHERE p.sticky_id = sn.id
                      )
                   OR EXISTS (
                          SELECT 1 FROM sticky_note_papers p
                          WHERE p.sticky_id = sn.id AND p.document_id = ANY(:ids)
                      )
                ORDER BY sn.pinned DESC, sn.updated_at DESC
            """),
            {"ids": document_ids or [None]},
        )
    return await _attach_papers(session, [dict(r) for r in result.mappings().all()])


async def get_sticky(session: AsyncSession, sticky_id: UUID) -> Optional[dict]:
    result = await session.execute(
        text("SELECT * FROM sticky_notes WHERE id = :id"), {"id": sticky_id}
    )
    row = result.mappings().first()
    if not row:
        return None
    return (await _attach_papers(session, [dict(row)]))[0]


async def _set_papers(
    session: AsyncSession, sticky_id: UUID, document_ids: list[UUID]
) -> None:
    await session.execute(
        text("DELETE FROM sticky_note_papers WHERE sticky_id = :id"), {"id": sticky_id}
    )
    for document_id in dict.fromkeys(document_ids):  # de-dupe, keep order
        await session.execute(
            text("""
                INSERT INTO sticky_note_papers (sticky_id, document_id)
                VALUES (:sticky_id, :document_id)
                ON CONFLICT DO NOTHING
            """),
            {"sticky_id": sticky_id, "document_id": document_id},
        )


async def create_sticky(
    session: AsyncSession,
    *,
    body: str,
    color: Optional[str] = None,
    pinned: bool = False,
    document_ids: Optional[list[UUID]] = None,
) -> dict:
    result = await session.execute(
        text("""
            INSERT INTO sticky_notes (body, color, pinned)
            VALUES (:body, :color, :pinned)
            RETURNING *
        """),
        {"body": body, "color": normalize_color(color), "pinned": pinned},
    )
    row = dict(result.mappings().one())
    await _set_papers(session, row["id"], document_ids or [])
    return (await _attach_papers(session, [row]))[0]


async def update_sticky(
    session: AsyncSession,
    sticky_id: UUID,
    *,
    body: Optional[str] = None,
    color: Optional[str] = None,
    pinned: Optional[bool] = None,
    document_ids: Optional[list[UUID]] = None,
) -> Optional[dict]:
    """Patch a sticky. Omitted fields are left alone.

    ⚠ ``document_ids=[]`` clears the scope (making the note global) while
    ``document_ids=None`` leaves it untouched. The two must stay distinguishable
    — collapsing them would make "this note is about nothing in particular"
    unreachable through the API.
    """
    sets = ["updated_at = NOW()"]
    params: dict = {"id": sticky_id}
    if body is not None:
        sets.append("body = :body")
        params["body"] = body
    if color is not None:
        sets.append("color = :color")
        params["color"] = normalize_color(color)
    if pinned is not None:
        sets.append("pinned = :pinned")
        params["pinned"] = pinned

    result = await session.execute(
        text(f"UPDATE sticky_notes SET {', '.join(sets)} WHERE id = :id RETURNING *"),
        params,
    )
    row = result.mappings().first()
    if not row:
        return None
    if document_ids is not None:
        await _set_papers(session, sticky_id, document_ids)
    return (await _attach_papers(session, [dict(row)]))[0]


async def delete_sticky(session: AsyncSession, sticky_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM sticky_notes WHERE id = :id"), {"id": sticky_id}
    )
    return (result.rowcount or 0) > 0
