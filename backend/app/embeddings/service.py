"""Embedding service: generate and store embeddings for chunks."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.database.repositories import embeddings as emb_repo
from app.embeddings.model import active_embedding_model, get_embeddings_batch

logger = get_logger(__name__)


async def embed_document_chunks(
    session: AsyncSession, document_id: UUID, batch_size: int = 20
) -> int:
    """Generate embeddings for all un-embedded chunks of a document."""
    total_embedded = 0

    while True:
        chunks = await emb_repo.get_chunks_without_embeddings(
            session, document_id, limit=batch_size
        )
        if not chunks:
            break

        texts = [c["plain_text"] for c in chunks]
        embeddings = await get_embeddings_batch(texts)
        model_name = await active_embedding_model()

        for chunk, embedding in zip(chunks, embeddings):
            await emb_repo.store_embedding(
                session,
                chunk_id=chunk["id"],
                embedding=embedding,
                model_name=model_name,
            )

        total_embedded += len(chunks)
        await session.commit()
        logger.info(f"Embedded {total_embedded} chunks for document {document_id}")

    return total_embedded

