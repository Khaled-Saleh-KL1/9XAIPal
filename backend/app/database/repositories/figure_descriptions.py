"""Repository for rich VLM-generated figure/diagram/architecture descriptions."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_figure_description(
    session: AsyncSession,
    *,
    document_id: UUID,
    chunk_id: UUID,
    image_path: str,
    description_markdown: str,
    description_plain: str,
    model: str,
    prompt_hash: str,
    source_sequence_start: Optional[int] = None,
    source_sequence_end: Optional[int] = None,
    referenced_by_chunk_ids: Optional[list[UUID]] = None,
) -> dict:
    """Insert or update a figure description (idempotent per chunk+model)."""
    result = await session.execute(
        text("""
            INSERT INTO figure_descriptions (
                document_id, chunk_id, image_path,
                description_markdown, description_plain,
                source_sequence_start, source_sequence_end,
                referenced_by_chunk_ids,
                model, prompt_hash
            )
            VALUES (
                :document_id, :chunk_id, :image_path,
                :description_markdown, :description_plain,
                :source_sequence_start, :source_sequence_end,
                :referenced_by_chunk_ids,
                :model, :prompt_hash
            )
            ON CONFLICT (chunk_id, model) DO UPDATE SET
                description_markdown = EXCLUDED.description_markdown,
                description_plain = EXCLUDED.description_plain,
                image_path = EXCLUDED.image_path,
                source_sequence_start = EXCLUDED.source_sequence_start,
                source_sequence_end = EXCLUDED.source_sequence_end,
                referenced_by_chunk_ids = EXCLUDED.referenced_by_chunk_ids,
                prompt_hash = EXCLUDED.prompt_hash,
                created_at = NOW()
            RETURNING id, chunk_id, model, created_at
        """),
        {
            "document_id": str(document_id),
            "chunk_id": str(chunk_id),
            "image_path": image_path,
            "description_markdown": description_markdown,
            "description_plain": description_plain,
            "source_sequence_start": source_sequence_start,
            "source_sequence_end": source_sequence_end,
            "referenced_by_chunk_ids": referenced_by_chunk_ids or [],
            "model": model,
            "prompt_hash": prompt_hash,
        },
    )
    return dict(result.mappings().one())


async def get_figure_descriptions_for_document(
    session: AsyncSession,
    document_id: UUID,
) -> list[dict]:
    result = await session.execute(
        text("""
            SELECT fd.*, c.sequence_id, c.page_start
            FROM figure_descriptions fd
            JOIN chunks c ON c.id = fd.chunk_id
            WHERE fd.document_id = :doc_id
            ORDER BY c.sequence_id ASC
        """),
        {"doc_id": str(document_id)},
    )
    return [dict(r) for r in result.mappings().all()]
