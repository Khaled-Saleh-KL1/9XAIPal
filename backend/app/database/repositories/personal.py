"""Personal reading state: bookmarks, the reader's own notes, and decks.

Everything here is per-document and user-owned. It is deliberately separate
from ``paper_notes``: an answer has a question, a model, citations and a
thread, and a bookmark has none of those.
"""

from uuid import UUID
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Bookmarks ───────────────────────────────────────────────────────────────


async def list_bookmarks(session: AsyncSession, document_id: UUID) -> list[dict]:
    """Every bookmark on a paper, in reading order."""
    result = await session.execute(
        text("""
            SELECT * FROM reading_bookmarks
            WHERE document_id = :document_id
            ORDER BY sequence_id ASC
        """),
        {"document_id": document_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def upsert_bookmark(
    session: AsyncSession,
    *,
    document_id: UUID,
    sequence_id: int,
    snippet: Optional[str] = None,
    kind: str = "block",
    page: Optional[int] = None,
    progress: float = 0.0,
    label: Optional[str] = None,
) -> dict:
    """Mark this block, or refresh the mark already on it.

    The unique constraint on (document_id, sequence_id) makes re-bookmarking a
    block an update rather than a second row: the reader gets one mark per
    place, which is the only thing "bookmark here" can sensibly mean.
    """
    result = await session.execute(
        text("""
            INSERT INTO reading_bookmarks
                (document_id, sequence_id, snippet, kind, page, progress, label)
            VALUES
                (:document_id, :sequence_id, :snippet, :kind, :page, :progress, :label)
            ON CONFLICT (document_id, sequence_id) DO UPDATE
                SET snippet    = EXCLUDED.snippet,
                    kind       = EXCLUDED.kind,
                    page       = EXCLUDED.page,
                    progress   = EXCLUDED.progress,
                    label      = COALESCE(EXCLUDED.label, reading_bookmarks.label),
                    updated_at = NOW()
            RETURNING *
        """),
        {
            "document_id": document_id,
            "sequence_id": sequence_id,
            "snippet": snippet,
            "kind": kind,
            "page": page,
            "progress": progress,
            "label": label,
        },
    )
    return dict(result.mappings().one())


async def get_bookmark(session: AsyncSession, bookmark_id: UUID) -> Optional[dict]:
    result = await session.execute(
        text("SELECT * FROM reading_bookmarks WHERE id = :id"),
        {"id": bookmark_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def set_bookmark_label(
    session: AsyncSession, bookmark_id: UUID, label: Optional[str]
) -> Optional[dict]:
    result = await session.execute(
        text("""
            UPDATE reading_bookmarks
            SET label = :label, updated_at = NOW()
            WHERE id = :id
            RETURNING *
        """),
        {"id": bookmark_id, "label": label},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def delete_bookmark(session: AsyncSession, bookmark_id: UUID) -> None:
    await session.execute(
        text("DELETE FROM reading_bookmarks WHERE id = :id"),
        {"id": bookmark_id},
    )


async def delete_bookmark_at(
    session: AsyncSession, document_id: UUID, sequence_id: int
) -> int:
    """Lift the mark off a block. Returns how many rows went."""
    result = await session.execute(
        text("""
            DELETE FROM reading_bookmarks
            WHERE document_id = :document_id AND sequence_id = :sequence_id
        """),
        {"document_id": document_id, "sequence_id": sequence_id},
    )
    return result.rowcount or 0


# ── Personal notes ──────────────────────────────────────────────────────────


async def list_personal_notes(session: AsyncSession, document_id: UUID) -> list[dict]:
    """Every personal note on a paper, ordered by anchor then time.

    Same ordering rule as paper_notes: the gutter lays cards out in one
    downward pass, so notes must arrive in the order of the blocks they
    point at.
    """
    result = await session.execute(
        text("""
            SELECT * FROM personal_notes
            WHERE document_id = :document_id
            ORDER BY anchor_sequence_id ASC, created_at ASC
        """),
        {"document_id": document_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_personal_note(session: AsyncSession, note_id: UUID) -> Optional[dict]:
    result = await session.execute(
        text("SELECT * FROM personal_notes WHERE id = :id"),
        {"id": note_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_personal_note(
    session: AsyncSession,
    *,
    document_id: UUID,
    anchor_sequence_id: int,
    body: str,
    anchor_chunk_id: Optional[UUID] = None,
    anchor_quote: Optional[str] = None,
    margin_side: str = "right",
) -> dict:
    result = await session.execute(
        text("""
            INSERT INTO personal_notes
                (document_id, anchor_chunk_id, anchor_sequence_id, anchor_quote,
                 body, margin_side)
            VALUES
                (:document_id, :anchor_chunk_id, :anchor_sequence_id, :anchor_quote,
                 :body, :margin_side)
            RETURNING *
        """),
        {
            "document_id": document_id,
            "anchor_chunk_id": anchor_chunk_id,
            "anchor_sequence_id": anchor_sequence_id,
            "anchor_quote": anchor_quote,
            "body": body,
            "margin_side": "left" if margin_side == "left" else "right",
        },
    )
    return dict(result.mappings().one())


async def update_personal_note(
    session: AsyncSession,
    note_id: UUID,
    *,
    body: Optional[str] = None,
    margin_side: Optional[str] = None,
) -> Optional[dict]:
    """Patch a note. Absent fields are left alone.

    COALESCE rather than a built-up SET list: the two fields change on
    different gestures (editing text, moving margins) and neither should
    disturb the other.
    """
    result = await session.execute(
        text("""
            UPDATE personal_notes
            SET body        = COALESCE(:body, body),
                margin_side = COALESCE(:margin_side, margin_side),
                updated_at  = NOW()
            WHERE id = :id
            RETURNING *
        """),
        {
            "id": note_id,
            "body": body,
            "margin_side": (
                None if margin_side not in ("left", "right") else margin_side
            ),
        },
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def delete_personal_note(session: AsyncSession, note_id: UUID) -> None:
    await session.execute(
        text("DELETE FROM personal_notes WHERE id = :id"),
        {"id": note_id},
    )


# ── Decks ───────────────────────────────────────────────────────────────────


async def prune_thin_decks(session: AsyncSession, document_id: UUID) -> int:
    """Drop decks that no longer hold two cards.

    Membership rows disappear on their own when the underlying note is deleted
    (both foreign keys cascade), so a deck can be reduced from the outside at
    any time. A deck of one is not a deck — its survivor belongs back in the
    margin on its own rather than hidden inside a stack of one.
    """
    result = await session.execute(
        text("""
            DELETE FROM note_decks
            WHERE document_id = :document_id
              AND (
                SELECT COUNT(*) FROM note_deck_members m WHERE m.deck_id = note_decks.id
              ) < 2
        """),
        {"document_id": document_id},
    )
    return result.rowcount or 0


async def list_decks(session: AsyncSession, document_id: UUID) -> list[dict]:
    """Every deck on a paper with its members, in stacking order."""
    result = await session.execute(
        text("""
            SELECT d.id, d.label, d.top_index, d.margin_side, d.study,
                   d.created_at, d.updated_at,
                   m.ordinal, m.ai_note_id, m.personal_note_id
            FROM note_decks d
            LEFT JOIN note_deck_members m ON m.deck_id = d.id
            WHERE d.document_id = :document_id
            ORDER BY d.created_at ASC, m.ordinal ASC
        """),
        {"document_id": document_id},
    )

    decks: dict[UUID, dict] = {}
    for row in result.mappings().all():
        deck = decks.setdefault(
            row["id"],
            {
                "id": row["id"],
                "label": row["label"],
                "top_index": row["top_index"],
                "margin_side": row["margin_side"],
                "study": row["study"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "members": [],
            },
        )
        if row["ai_note_id"] is not None:
            deck["members"].append({"kind": "ai", "id": row["ai_note_id"]})
        elif row["personal_note_id"] is not None:
            deck["members"].append({"kind": "personal", "id": row["personal_note_id"]})
    return list(decks.values())


def _valid_arrangement(decks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce a desired arrangement to a storable one.

    Two rules, both of which the database also enforces — a card belongs to at
    most one deck, and a deck of fewer than two cards is not a deck. Applied
    here rather than left to the caller so that the unique index is a backstop
    against a bug rather than the only thing standing between a client
    round-off and a 500.
    """
    claimed: set = set()
    out: list[dict[str, Any]] = []

    for deck in decks:
        members = []
        for member in deck.get("members") or []:
            if member["id"] in claimed:
                continue
            claimed.add(member["id"])
            members.append(member)

        if len(members) < 2:
            continue

        top = max(0, int(deck.get("top_index") or 0))
        out.append({
            **deck,
            "members": members,
            "top_index": min(top, len(members) - 1),
        })
    return out


async def replace_decks(
    session: AsyncSession, document_id: UUID, decks: list[dict[str, Any]]
) -> None:
    """Overwrite this paper's deck arrangement with the one supplied.

    ⚠ Whole-collection replace, not per-deck CRUD, and that is deliberate. One
    drag can dissolve a deck, create another, and move a card between two more
    — a single arrangement computed in one place. Expressed as granular calls
    it becomes an ordered sequence with a half-applied state at every step,
    and a dropped request in the middle leaves a card in two decks or none.

    Deck ids supplied by the caller are preserved so that untouched decks keep
    their identity (and their created_at ordering) across a write.

    ⚠ Members are cleared for the whole document before any are inserted. The
    unique index that stops a card belonging to two decks does not care that
    the row it collides with is one this same statement is about to delete, so
    swapping two cards between two decks fails unless the slate is wiped first.
    """
    decks = _valid_arrangement(decks)
    keep = [d["id"] for d in decks if d.get("id")]

    if keep:
        await session.execute(
            text("""
                DELETE FROM note_decks
                WHERE document_id = :document_id AND id <> ALL(:keep)
            """),
            {"document_id": document_id, "keep": keep},
        )
    else:
        await session.execute(
            text("DELETE FROM note_decks WHERE document_id = :document_id"),
            {"document_id": document_id},
        )

    if not decks:
        return

    await session.execute(
        text("""
            DELETE FROM note_deck_members
            WHERE deck_id IN (SELECT id FROM note_decks WHERE document_id = :document_id)
        """),
        {"document_id": document_id},
    )

    for deck in decks:
        await session.execute(
            text("""
                INSERT INTO note_decks
                    (id, document_id, label, top_index, margin_side, study)
                VALUES
                    (:id, :document_id, :label, :top_index, :margin_side, :study)
                ON CONFLICT (id) DO UPDATE
                    SET label       = EXCLUDED.label,
                        top_index   = EXCLUDED.top_index,
                        margin_side = EXCLUDED.margin_side,
                        study       = EXCLUDED.study,
                        updated_at  = NOW()
            """),
            {
                "id": deck["id"],
                "document_id": document_id,
                "label": deck.get("label"),
                "top_index": max(0, int(deck.get("top_index") or 0)),
                "margin_side": "left" if deck.get("margin_side") == "left" else "right",
                "study": bool(deck.get("study")),
            },
        )

        for ordinal, member in enumerate(deck.get("members") or []):
            await session.execute(
                text("""
                    INSERT INTO note_deck_members
                        (deck_id, ordinal, ai_note_id, personal_note_id)
                    VALUES
                        (:deck_id, :ordinal, :ai_note_id, :personal_note_id)
                """),
                {
                    "deck_id": deck["id"],
                    "ordinal": ordinal,
                    "ai_note_id": member["id"] if member["kind"] == "ai" else None,
                    "personal_note_id": (
                        member["id"] if member["kind"] == "personal" else None
                    ),
                },
            )


async def owned_note_ids(session: AsyncSession, document_id: UUID) -> tuple[set, set]:
    """The AI-note and personal-note ids that belong to this paper.

    Used to reject deck members pointing at another document's notes before
    they reach a foreign key that would happily accept them.
    """
    ai = await session.execute(
        text("SELECT id FROM paper_notes WHERE document_id = :d AND parent_note_id IS NULL"),
        {"d": document_id},
    )
    personal = await session.execute(
        text("SELECT id FROM personal_notes WHERE document_id = :d"),
        {"d": document_id},
    )
    return (
        {r[0] for r in ai.all()},
        {r[0] for r in personal.all()},
    )
