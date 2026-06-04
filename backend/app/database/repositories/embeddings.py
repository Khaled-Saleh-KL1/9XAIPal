"""Embedding repository: metadata persistence delegating vector ops to pgvector."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.pgvector import insert_embedding, search_similar_chunks


async def store_embedding(
    session: AsyncSession,
    chunk_id: UUID,
    embedding: list[float],
    model_name: str,
) -> None:
    """Store a chunk embedding via pgvector."""
    await insert_embedding(session, chunk_id, embedding, model_name)


async def search_embeddings(
    session: AsyncSession,
    query_embedding: list[float],
    limit: int = 10,
    document_id: UUID | None = None,
) -> list[dict]:
    """Search similar chunks via pgvector."""
    return await search_similar_chunks(session, query_embedding, limit, document_id)


async def get_chunks_without_embeddings(
    session: AsyncSession, document_id: UUID, limit: int = 100
) -> list[dict]:
    """Find chunks that don't have embeddings yet."""
    result = await session.execute(
        text("""
            SELECT c.id, c.plain_text FROM chunks c
            LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id
            WHERE c.document_id = :document_id AND ce.chunk_id IS NULL
            ORDER BY c.sequence_id
            LIMIT :limit
        """),
        {"document_id": document_id, "limit": limit},
    )
    return [dict(r) for r in result.mappings().all()]

