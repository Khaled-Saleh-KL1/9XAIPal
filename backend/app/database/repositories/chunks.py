"""Chunk repository: sequential retrieval and persistence."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_chunks(session: AsyncSession, chunks: list[dict]) -> list[dict]:
    """Bulk insert chunks for a document."""
    results = []
    for chunk in chunks:
        result = await session.execute(
            text("""
                INSERT INTO chunks
                    (document_id, sequence_id, parent_sequence_id, chunk_type,
                     heading_path, markdown, plain_text, page_start, page_end,
                     bbox_json, token_count, table_json)
                VALUES
                    (:document_id, :sequence_id, :parent_sequence_id, :chunk_type,
                     :heading_path, :markdown, :plain_text, :page_start, :page_end,
                     :bbox_json, :token_count, :table_json)
                RETURNING id, document_id, sequence_id, chunk_type, created_at
            """),
            chunk,
        )
        results.append(dict(result.mappings().one()))
    return results


async def get_chunk(session: AsyncSession, chunk_id: UUID) -> Optional[dict]:
    """Fetch a chunk by ID."""
    result = await session.execute(
        text("SELECT * FROM chunks WHERE id = :id"),
        {"id": chunk_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_chunk_by_sequence(
    session: AsyncSession, document_id: UUID, sequence_id: int
) -> Optional[dict]:
    """Fetch a chunk by document_id + sequence_id."""
    result = await session.execute(
        text("""
            SELECT * FROM chunks
            WHERE document_id = :document_id AND sequence_id = :sequence_id
        """),
        {"document_id": document_id, "sequence_id": sequence_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_next_chunk(
    session: AsyncSession, document_id: UUID, current_sequence_id: int
) -> Optional[dict]:
    """Fetch the next chunk in physical order."""
    result = await session.execute(
        text("""
            SELECT * FROM chunks
            WHERE document_id = :document_id AND sequence_id > :seq
            ORDER BY sequence_id ASC
            LIMIT 1
        """),
        {"document_id": document_id, "seq": current_sequence_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_previous_chunk(
    session: AsyncSession, document_id: UUID, current_sequence_id: int
) -> Optional[dict]:
    """Fetch the previous chunk in physical order."""
    result = await session.execute(
        text("""
            SELECT * FROM chunks
            WHERE document_id = :document_id AND sequence_id < :seq
            ORDER BY sequence_id DESC
            LIMIT 1
        """),
        {"document_id": document_id, "seq": current_sequence_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_chunk_window(
    session: AsyncSession,
    document_id: UUID,
    center_sequence_id: int,
    window_size: int = 2,
) -> list[dict]:
    """Fetch a window of chunks around a center sequence_id."""
    result = await session.execute(
        text("""
            SELECT * FROM chunks
            WHERE document_id = :document_id
              AND sequence_id BETWEEN :start AND :end
            ORDER BY sequence_id ASC
        """),
        {
            "document_id": document_id,
            "start": center_sequence_id - window_size,
            "end": center_sequence_id + window_size,
        },
    )
    return [dict(r) for r in result.mappings().all()]


async def get_document_chunks(
    session: AsyncSession,
    document_id: UUID,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List chunks for a document in sequence order."""
    result = await session.execute(
        text("""
            SELECT * FROM chunks
            WHERE document_id = :document_id
            ORDER BY sequence_id ASC
            LIMIT :limit OFFSET :offset
        """),
        {"document_id": document_id, "limit": limit, "offset": offset},
    )
    return [dict(r) for r in result.mappings().all()]


async def count_document_chunks(session: AsyncSession, document_id: UUID) -> int:
    """Total number of chunks for the given document."""
    result = await session.execute(
        text("SELECT COUNT(*) AS n FROM chunks WHERE document_id = :document_id"),
        {"document_id": document_id},
    )
    row = result.mappings().first()
    return int(row["n"]) if row else 0


async def get_chapter_headings(session: AsyncSession, document_id: UUID) -> list[dict]:
    """Return every heading chunk with its level (heading_path depth), in order.

    The caller decides which level constitutes a "chapter": MinerU often marks
    the document title as the sole level-1 heading and the real sections as
    level-2, so chapters must be derived from the shallowest level that actually
    partitions the document, not from level-1 alone.
    """
    result = await session.execute(
        text("""
            SELECT sequence_id, plain_text, markdown,
                   array_length(heading_path, 1) AS level
            FROM chunks
            WHERE document_id = :document_id
              AND chunk_type = 'heading'
              AND heading_path IS NOT NULL
              AND array_length(heading_path, 1) >= 1
            ORDER BY sequence_id ASC
        """),
        {"document_id": document_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_sequence_bounds(session: AsyncSession, document_id: UUID) -> tuple[int, int]:
    """Return (min_sequence, max_sequence) for the document's chunks (0,0 if none)."""
    result = await session.execute(
        text("""
            SELECT COALESCE(MIN(sequence_id), 0) AS lo, COALESCE(MAX(sequence_id), 0) AS hi
            FROM chunks WHERE document_id = :document_id
        """),
        {"document_id": document_id},
    )
    row = result.mappings().first()
    return (int(row["lo"]), int(row["hi"])) if row else (0, 0)

