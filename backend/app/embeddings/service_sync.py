"""Synchronous embedding service: generate and store embeddings for chunks in committed batches."""

from uuid import UUID
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.config import settings
from app.embeddings.model import active_embedding_model_sync, get_embeddings_batch_sync

logger = get_logger(__name__)


# Embedding input sizing: Ollama's /api/embed returns a hard 400 ("input
# length exceeds the context length") rather than truncating — even with
# truncate=true. Dense tables tokenize heavily, so the cap is conservative.
# Configurable via EMBED_MAX_CHARS (cloud embedders allow much more).


def get_chunks_without_embeddings_sync(session: Session, document_id: UUID, limit: int = 20) -> list[dict]:
    """Retrieve chunks without embeddings synchronously."""
    result = session.execute(
        text("""
            SELECT c.id, c.plain_text, c.chunk_type FROM chunks c
            LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id
            WHERE c.document_id = :document_id AND ce.chunk_id IS NULL
            ORDER BY c.sequence_id
            LIMIT :limit
        """),
        {"document_id": document_id, "limit": limit},
    )
    return [dict(r) for r in result.mappings().all()]


def _embed_text_for_chunk(chunk: dict) -> str:
    """Build a safe, non-empty, length-capped text to embed for a chunk.

    Empty plain_text (e.g. figures with no caption) would make Ollama's
    /api/embed return 400 and stall the whole batch, so substitute a small
    placeholder; oversized text is truncated to stay within the model's window.
    """
    txt = (chunk.get("plain_text") or "").strip()
    if not txt:
        txt = f"[{chunk.get('chunk_type') or 'content'}]"
    return txt[:settings.embed_max_chars]


def embed_document_chunks_sync(
    session: Session, document_id: UUID, batch_size: int = 20
) -> int:
    """Generate embeddings for all un-embedded chunks of a document in committed batches."""
    total_embedded = 0

    while True:
        chunks = get_chunks_without_embeddings_sync(session, document_id, limit=batch_size)
        if not chunks:
            break

        texts = [_embed_text_for_chunk(c) for c in chunks]
        embeddings = get_embeddings_batch_sync(texts)
        model_name = active_embedding_model_sync()

        if not embeddings or len(embeddings) != len(chunks):
            raise ValueError(
                f"Generated embedding count ({len(embeddings) if embeddings else 0}) "
                f"does not match chunk count ({len(chunks)})"
            )

        # Prepare payloads with explicit casting of elements to python float
        payloads = []
        for chunk, embedding in zip(chunks, embeddings):
            cast_embedding = [float(v) for v in embedding]
            payloads.append({
                "chunk_id": chunk["id"],
                "embedding": cast_embedding,  # Explicit list of floats matching pgvector extension dialect
                "model": model_name,
            })

        # Insert batch into database
        session.execute(
            text("""
                INSERT INTO chunk_embeddings (chunk_id, embedding, embedding_model)
                VALUES (:chunk_id, :embedding, :model)
                ON CONFLICT (chunk_id) DO UPDATE
                SET embedding = EXCLUDED.embedding,
                    embedding_model = EXCLUDED.embedding_model,
                    created_at = NOW()
            """),
            payloads,
        )

        total_embedded += len(chunks)
        session.commit()
        logger.info(f"Embedded {total_embedded} chunks synchronously for document {document_id}")

    return total_embedded
