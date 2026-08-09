"""Paper-note repository: anchored margin annotations."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_note(
    session: AsyncSession,
    *,
    document_id: UUID,
    anchor_sequence_id: int,
    question: str,
    anchor_chunk_id: Optional[UUID] = None,
    anchor_kind: str = "text",
    anchor_quote: Optional[str] = None,
    anchor_image_path: Optional[str] = None,
    parent_note_id: Optional[UUID] = None,
    margin_side: str = "right",
    requested_model: Optional[str] = None,
) -> dict:
    """Insert the note with an empty answer.

    The row is created BEFORE generation starts so the card has a stable id to
    stream into and the question survives even if the model call fails.
    """
    result = await session.execute(
        text("""
            INSERT INTO paper_notes
                (document_id, anchor_chunk_id, anchor_sequence_id, anchor_kind,
                 anchor_quote, anchor_image_path, question, parent_note_id,
                 margin_side, requested_model)
            VALUES
                (:document_id, :anchor_chunk_id, :anchor_sequence_id, :anchor_kind,
                 :anchor_quote, :anchor_image_path, :question, :parent_note_id,
                 :margin_side, :requested_model)
            RETURNING *
        """),
        {
            "document_id": document_id,
            "anchor_chunk_id": anchor_chunk_id,
            "anchor_sequence_id": anchor_sequence_id,
            "anchor_kind": anchor_kind,
            "anchor_quote": anchor_quote,
            "anchor_image_path": anchor_image_path,
            "question": question,
            "parent_note_id": parent_note_id,
            "margin_side": "left" if margin_side == "left" else "right",
            "requested_model": requested_model,
        },
    )
    return dict(result.mappings().one())


async def set_margin_side(session: AsyncSession, note_id: UUID, side: str) -> None:
    """Move a note to the other margin."""
    await session.execute(
        text("UPDATE paper_notes SET margin_side = :side WHERE id = :id"),
        {"id": note_id, "side": "left" if side == "left" else "right"},
    )


async def finalize_note(
    session: AsyncSession,
    note_id: UUID,
    *,
    answer: str,
    model: Optional[str],
    retrieval_mode: Optional[str],
    cited_sequence_ids: Optional[list[int]],
) -> None:
    """Write the generated answer back onto the note."""
    await session.execute(
        text("""
            UPDATE paper_notes
            SET answer = :answer,
                model = :model,
                retrieval_mode = :retrieval_mode,
                cited_sequence_ids = :cited
            WHERE id = :id
        """),
        {
            "id": note_id,
            "answer": answer,
            "model": model,
            "retrieval_mode": retrieval_mode,
            "cited": cited_sequence_ids or None,
        },
    )


async def list_notes(session: AsyncSession, document_id: UUID) -> list[dict]:
    """Every note on a paper, ordered by anchor position then time.

    Ordering by anchor first is what lets the reader lay the gutter out in a
    single downward pass: notes arrive in the same order as the blocks they
    point at.
    """
    result = await session.execute(
        text("""
            SELECT * FROM paper_notes
            WHERE document_id = :document_id
            ORDER BY anchor_sequence_id ASC, created_at ASC
        """),
        {"document_id": document_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_note(session: AsyncSession, note_id: UUID) -> Optional[dict]:
    result = await session.execute(
        text("SELECT * FROM paper_notes WHERE id = :id"),
        {"id": note_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_note_thread(session: AsyncSession, note_id: UUID) -> list[dict]:
    """The note plus every follow-up chained to it, oldest first.

    Used to give a follow-up question the context of what was already asked and
    answered at this anchor, so "why?" means something.
    """
    result = await session.execute(
        text("""
            WITH RECURSIVE thread AS (
                SELECT * FROM paper_notes WHERE id = :id
                UNION ALL
                SELECT n.* FROM paper_notes n
                JOIN thread t ON n.parent_note_id = t.id
            )
            SELECT * FROM thread ORDER BY created_at ASC
        """),
        {"id": note_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def delete_note(session: AsyncSession, note_id: UUID) -> None:
    """Delete a note; follow-ups cascade with it."""
    await session.execute(
        text("DELETE FROM paper_notes WHERE id = :id"),
        {"id": note_id},
    )
