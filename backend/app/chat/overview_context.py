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
    max_sequence_id: Optional[int] = None,
) -> dict:
    """Build the special OVERVIEW context block for the chat orchestrator.

    ``max_sequence_id`` is the reader's progress ceiling. This route is the
    single worst spoiler in the app when it is ignored: these summaries are
    pre-computed over the WHOLE document, so "what is this about?" on page 40
    of a book otherwise answers with the ending. A section that starts beyond
    the ceiling is dropped entirely, and the level-0 whole-document overview
    is withheld unless the reader has actually reached the end — a summary of
    the entire book IS the spoiler, however it is phrased.
    """
    summaries = await get_section_summaries(session, document_id)

    if max_sequence_id is not None:
        summaries = [
            s for s in summaries
            if s.get("sequence_start") is None
            or s["sequence_start"] <= max_sequence_id
        ]
        # Trim a section the reader is only part-way into: keep it (they have
        # started it) but mark it, so the formatter can say so rather than
        # presenting a whole-section summary as if it were all read.
        for s in summaries:
            end = s.get("sequence_end")
            if end is not None and end > max_sequence_id:
                s["partially_read"] = True

    # Separate paper overview from section summaries
    paper_overview = next((s for s in summaries if s.get("level") == 0), None)
    if paper_overview is not None and max_sequence_id is not None:
        end = paper_overview.get("sequence_end")
        if end is not None and end > max_sequence_id:
            paper_overview = None
    section_summaries = [s for s in summaries if s.get("level") in (1, 2)]

    return {
        "paper_overview": paper_overview,
        "section_summaries": section_summaries,
        "total": len(section_summaries) + (1 if paper_overview else 0),
    }
