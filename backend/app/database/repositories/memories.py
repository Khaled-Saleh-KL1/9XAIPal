"""Agent memory repository: metadata persistence delegating vector ops to pgvector."""

from uuid import UUID
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.pgvector import find_similar_memory, insert_memory, search_memories


async def remember(
    session: AsyncSession,
    *,
    user_id: UUID,
    body: str,
    embedding: list[float],
    model_name: str,
    document_id: Optional[UUID] = None,
    source: str = "explicit",
) -> UUID:
    """Store one durable memory via pgvector."""
    return await insert_memory(session, user_id, document_id, body, source, embedding, model_name)


async def recall(
    session: AsyncSession,
    *,
    user_id: UUID,
    query_embedding: list[float],
    document_id: Optional[UUID] = None,
    limit: int = 5,
    min_similarity: float = 0.45,
) -> list[dict]:
    """Search relevant memories via pgvector."""
    return await search_memories(session, user_id, query_embedding, limit, document_id, min_similarity)


async def find_duplicate(
    session: AsyncSession,
    *,
    user_id: UUID,
    query_embedding: list[float],
    document_id: Optional[UUID] = None,
    min_similarity: float = 0.92,
) -> Optional[dict]:
    """The closest existing memory in this exact scope, if near-identical."""
    return await find_similar_memory(session, user_id, document_id, query_embedding, min_similarity)
