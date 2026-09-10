"""Repository for a paper's own bibliography entries (see services/references.py
for the parser and search/semantic_scholar_client.py for resolution)."""

import json
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_references(session: AsyncSession, document_id: UUID) -> list[dict]:
    """This paper's cached bibliography entries, in citation-number order."""
    result = await session.execute(
        text("SELECT * FROM paper_references WHERE document_id = :document_id ORDER BY ref_number"),
        {"document_id": document_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def bulk_insert_pending(session: AsyncSession, document_id: UUID, entries: list[dict]) -> None:
    """Seed one row per parsed entry with resolve_status='pending' (the column
    default), first call only — see the UNIQUE(document_id, ref_number)
    constraint this relies on. `entries` is `parse_references`'s own output.
    """
    if not entries:
        return
    await session.execute(
        text("""
            INSERT INTO paper_references (document_id, ref_number, raw_text)
            VALUES (:document_id, :ref_number, :raw_text)
            ON CONFLICT (document_id, ref_number) DO NOTHING
        """),
        [
            {"document_id": document_id, "ref_number": e["number"], "raw_text": e["raw_text"]}
            for e in entries
        ],
    )


async def get_reference(session: AsyncSession, document_id: UUID, ref_number: int) -> Optional[dict]:
    result = await session.execute(
        text("SELECT * FROM paper_references WHERE document_id = :document_id AND ref_number = :ref_number"),
        {"document_id": document_id, "ref_number": ref_number},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def save_resolution(
    session: AsyncSession,
    document_id: UUID,
    ref_number: int,
    *,
    status: str,
    title: Optional[str] = None,
    authors: Optional[str] = None,
    year: Optional[int] = None,
    pdf_url: Optional[str] = None,
    external_ids: Optional[dict] = None,
) -> dict:
    """Persist a resolve attempt's outcome — 'resolved', 'no_match', or
    'unavailable' (see semantic_scholar_client.match_reference's docstring
    for what collapses into the latter two)."""
    result = await session.execute(
        text("""
            UPDATE paper_references
            SET resolve_status = :status,
                resolved_title = :title,
                resolved_authors = :authors,
                resolved_year = :year,
                resolved_pdf_url = :pdf_url,
                external_ids = CAST(:external_ids AS JSONB)
            WHERE document_id = :document_id AND ref_number = :ref_number
            RETURNING *
        """),
        {
            "document_id": document_id,
            "ref_number": ref_number,
            "status": status,
            "title": title,
            "authors": authors,
            "year": year,
            "pdf_url": pdf_url,
            "external_ids": json.dumps(external_ids) if external_ids is not None else None,
        },
    )
    return dict(result.mappings().one())


async def mark_added(
    session: AsyncSession, document_id: UUID, ref_number: int, added_document_id: UUID
) -> None:
    """Record that this reference's resolved paper is now in the library —
    what makes a future read answer "already added" without re-matching."""
    await session.execute(
        text("""
            UPDATE paper_references SET added_document_id = :added_document_id
            WHERE document_id = :document_id AND ref_number = :ref_number
        """),
        {"document_id": document_id, "ref_number": ref_number, "added_document_id": added_document_id},
    )
