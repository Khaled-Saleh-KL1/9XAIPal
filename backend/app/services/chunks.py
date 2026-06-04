"""Chunk service: sequential navigation."""

from uuid import UUID
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import chunks as chunk_repo


async def get_chunk(session: AsyncSession, chunk_id: UUID) -> Optional[dict]:
    """Fetch a single chunk."""
    return await chunk_repo.get_chunk(session, chunk_id)


async def get_next_chunk(
    session: AsyncSession, document_id: UUID, current_chunk_id: UUID
) -> Optional[dict]:
    """Fetch the next chunk in physical order."""
    current = await chunk_repo.get_chunk(session, current_chunk_id)
    if not current:
        return None
    return await chunk_repo.get_next_chunk(session, document_id, current["sequence_id"])


async def get_previous_chunk(
    session: AsyncSession, document_id: UUID, current_chunk_id: UUID
) -> Optional[dict]:
    """Fetch the previous chunk in physical order."""
    current = await chunk_repo.get_chunk(session, current_chunk_id)
    if not current:
        return None
    return await chunk_repo.get_previous_chunk(session, document_id, current["sequence_id"])


async def get_chunk_window(
    session: AsyncSession,
    document_id: UUID,
    chunk_id: UUID,
    window_size: int = 2,
) -> list[dict]:
    """Fetch a window of chunks around the given chunk."""
    current = await chunk_repo.get_chunk(session, chunk_id)
    if not current:
        return []
    return await chunk_repo.get_chunk_window(
        session, document_id, current["sequence_id"], window_size
    )


async def get_document_chunks(
    session: AsyncSession, document_id: UUID, limit: int = 100, offset: int = 0
) -> list[dict]:
    """List chunks for a document."""
    return await chunk_repo.get_document_chunks(session, document_id, limit, offset)

