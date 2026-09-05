"""Repository helpers for the section_summaries table (pre-computed high-quality overviews)."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_section_summaries_for_document(
    session: AsyncSession,
    document_id: UUID,
) -> list[dict]:
    """Return all summaries for a document ordered by structure."""
    result = await session.execute(
        text("""
            SELECT *
            FROM section_summaries
            WHERE document_id = :doc_id
            ORDER BY level ASC, sequence_start ASC NULLS LAST, created_at ASC
        """),
        {"doc_id": str(document_id)},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_gists_for_documents(
    session: AsyncSession,
    document_ids: list[UUID],
) -> dict:
    """One short description per summarised section, for several documents.

    Keyed ``(document_id, sequence_start)`` so a caller holding a heading can
    look up what that section is about; the whole-document overview (level 0,
    which has no meaningful sequence_start) is keyed ``(document_id, None)``.

    Only the columns the study index needs, and only levels 0-2 — the point is
    a line beside a heading, not the summary itself. Reading the full
    ``summary_markdown`` for 24 papers to print one clause of each would move
    megabytes to throw nearly all of it away.
    """
    if not document_ids:
        return {}
    result = await session.execute(
        text("""
            SELECT document_id, level, sequence_start, summary_plain
            FROM section_summaries
            WHERE document_id = ANY(:doc_ids) AND summary_plain IS NOT NULL
            ORDER BY level ASC, sequence_start ASC NULLS FIRST
        """),
        {"doc_ids": list(document_ids)},
    )
    gists: dict = {}
    for row in result.mappings().all():
        key = (row["document_id"], None if row["level"] == 0 else row["sequence_start"])
        # ORDER BY puts level 0 first and lower sequence_start first; keep the
        # first hit for a key so a re-summarised section cannot shadow it.
        gists.setdefault(key, row["summary_plain"])
    return gists


async def count_section_summaries(
    session: AsyncSession,
    document_id: UUID,
) -> int:
    result = await session.execute(
        text("SELECT COUNT(*) AS n FROM section_summaries WHERE document_id = :doc_id"),
        {"doc_id": str(document_id)},
    )
    row = result.mappings().first()
    return int(row["n"]) if row else 0
