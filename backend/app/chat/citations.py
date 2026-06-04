"""Citation formatting for chunks and external sources."""

from typing import Optional
from uuid import UUID

from app.schemas.chat import Citation


def citations_from_chunks(chunks: list[dict]) -> list[Citation]:
    """Create citations from retrieved chunks."""
    citations = []
    for c in chunks:
        citations.append(Citation(
            chunk_id=c.get("id"),
            sequence_id=c.get("sequence_id"),
            page=c.get("page_start"),
            text_snippet=c.get("plain_text", "")[:200] if c.get("plain_text") else None,
            source="document",
        ))
    return citations


def citations_from_web_results(results: list[dict]) -> list[Citation]:
    """Create citations from web search results."""
    citations = []
    for r in results:
        citations.append(Citation(
            url=r.get("url"),
            text_snippet=r.get("snippet", "")[:200],
            source=r.get("source_engine", "web"),
        ))
    return citations


def citations_from_overview(overview_ctx: dict) -> list[Citation]:
    """
    Create citations from the pre-computed section summaries.

    Each summary carries `source_chunk_ids`. We emit one citation per summary
    using the first source chunk as the primary anchor (sequence/page info
    will be resolved by the frontend or future enrichment).
    """
    citations: list[Citation] = []

    # Paper overview (level 0) — usually has no direct chunk sources
    paper_ov = overview_ctx.get("paper_overview")
    if paper_ov:
        citations.append(Citation(
            text_snippet=(paper_ov.get("summary_plain") or "")[:220],
            source="paper_overview",
        ))

    for s in overview_ctx.get("section_summaries") or []:
        src_ids = s.get("source_chunk_ids") or []
        citations.append(Citation(
            chunk_id=src_ids[0] if src_ids else None,
            sequence_id=s.get("sequence_start"),
            text_snippet=(s.get("summary_plain") or "")[:220],
            source="section_summary",
        ))

    return citations

