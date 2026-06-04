"""Overview context builder: returns pre-computed hierarchical section summaries.

This is the high-quality path for "Summarize the paper", "What is this about?",
"main contributions?", etc. It completely bypasses vector search and returns
the rich, attributed summaries produced by the summarization Celery task.

Because the author built this for personal use and explicitly accepts long
ingestion times, these summaries can be as high-quality and detailed as we want.
"""

from uuid import UUID
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


async def get_section_summaries(
    session: AsyncSession,
    document_id: UUID,
    *,
    include_paper_overview: bool = True,
) -> list[dict]:
    """
    Return all section summaries for a document, ordered by document structure.

    Level 0 (paper overview) comes first if present, then level 1, then level 2.
    """
    result = await session.execute(
        text("""
            SELECT
                id, section_id, level, heading_path,
                sequence_start, sequence_end,
                summary_markdown, summary_plain,
                source_chunk_ids,
                model, created_at
            FROM section_summaries
            WHERE document_id = :doc_id
            ORDER BY
                level ASC,                    -- paper overview (0) first
                sequence_start ASC NULLS LAST,
                created_at ASC
        """),
        {"doc_id": str(document_id)},
    )
    rows = [dict(r) for r in result.mappings().all()]

    if not include_paper_overview:
        rows = [r for r in rows if r.get("level") != 0]

    return rows


async def build_overview_context(
    session: AsyncSession,
    *,
    document_id: UUID,
) -> dict:
    """Build the special OVERVIEW context block for the chat orchestrator."""
    summaries = await get_section_summaries(session, document_id)

    # Separate paper overview from section summaries
    paper_overview = next((s for s in summaries if s.get("level") == 0), None)
    section_summaries = [s for s in summaries if s.get("level") in (1, 2)]

    return {
        "paper_overview": paper_overview,
        "section_summaries": section_summaries,
        "total": len(summaries),
    }
