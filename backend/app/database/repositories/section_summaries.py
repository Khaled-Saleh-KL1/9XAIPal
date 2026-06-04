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
