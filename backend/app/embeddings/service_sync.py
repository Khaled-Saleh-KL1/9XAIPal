"""Synchronous embedding service: generate and store embeddings for chunks in committed batches."""

from concurrent.futures import ThreadPoolExecutor
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


def get_chunks_without_embeddings_sync(
    session: Session,
    document_id: UUID,
    limit: int | None = 20,
    *,
    include_existing: bool = False,
    chunk_type: str | None = None,
) -> list[dict]:
    """Retrieve chunks that need embedding work synchronously.

    The normal ingestion path asks only for missing rows. A repair can set
    ``include_existing`` to regenerate every chunk while the old vector stays
    available until its replacement is committed. The chunk id remains the
    sole upsert key, so a repair can never write one document's vector over a
    different document's chunk.
    """
    limit_sql = "" if limit is None else "LIMIT :limit"
    missing_sql = "" if include_existing else "AND ce.chunk_id IS NULL"
    type_sql = "" if chunk_type is None else "AND c.chunk_type = :chunk_type"
    result = session.execute(
        text(f"""
            SELECT c.id, c.plain_text, c.chunk_type,
                   fd.description_plain AS figure_description
            FROM chunks c
            LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id
            LEFT JOIN LATERAL (
                SELECT description_plain
                FROM figure_descriptions
                WHERE chunk_id = c.id
                ORDER BY created_at DESC
                LIMIT 1
            ) fd ON TRUE
            WHERE c.document_id = :document_id {missing_sql} {type_sql}
            ORDER BY c.sequence_id
            {limit_sql}
        """),
        {
            "document_id": document_id,
            **({"limit": limit} if limit is not None else {}),
            **({"chunk_type": chunk_type} if chunk_type is not None else {}),
        },
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
    # Figure descriptions are retrieval-only metadata. Keep them out of the
    # chunk row (and therefore out of the reader/API), but include them in the
    # figure's vector input so a question about what a diagram shows can find
    # the original image.
    if chunk.get("chunk_type") == "figure":
        description = (chunk.get("figure_description") or "").strip()
        if description:
            txt = f"{txt}\n\nFigure description:\n{description}"
    return txt[:settings.embed_max_chars]


def _embed_batch(batch: list[dict]) -> tuple[list[dict], list[list[float]] | None, Exception | None]:
    """Embed one sub-batch. Isolated per-batch so one failure doesn't kill the pool."""
    texts = [_embed_text_for_chunk(c) for c in batch]
    try:
        return batch, get_embeddings_batch_sync(texts), None
    except Exception as exc:
        return batch, None, exc


def _persist_batch(session: Session, model_name: str, batch: list[dict], embeddings: list[list[float]]) -> None:
    """Insert one sub-batch's embeddings and commit — called on the main thread only."""
    payloads = [
        {
            "chunk_id": chunk["id"],
            "embedding": [float(v) for v in embedding],  # explicit floats matching pgvector's dialect
            "model": model_name,
        }
        for chunk, embedding in zip(batch, embeddings)
    ]
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
    session.commit()


def embed_document_chunks_sync(
    session: Session,
    document_id: UUID,
    batch_size: int = 20,
    *,
    force: bool = False,
    chunk_type: str | None = None,
) -> int:
    """Generate embeddings for all un-embedded chunks of a document in committed batches.

    Sub-batches run concurrently against the embedding backend. Measured on
    this deployment's local Ollama (qwen3-embedding:0.6b, 6 CPU cores): a
    single batch request only occupies ~2.8 cores, and one-request-per-chunk
    is ~13x slower than batching — so a few requests in flight at once uses
    the box's other idle cores, the same reasoning already applied to VLM
    figure descriptions (see figure_describer_sync.py / vlm_max_concurrency).

    Persistence still happens one sub-batch at a time, each on its own commit
    — a Session isn't thread-safe, so writes can't happen from the pool
    threads, and batching them into one commit per wave would mean a single
    failing sub-batch loses every OTHER sub-batch's already-finished work in
    that wave too. `pool.map` submits every sub-batch at once (so they still
    run concurrently) but yields results in submission order rather than
    completion order, so committing as each is yielded is deterministic and
    keeps the original guarantee: a crash mid-document only loses the batch
    that was actually in flight, not batches that already finished.
    """
    total_embedded = 0
    workers = max(1, settings.embedding_max_concurrency)


    # Take a stable snapshot for forced repairs; re-querying for missing rows
    # would return the same rows forever after each upsert.
    forced_chunks = (
        get_chunks_without_embeddings_sync(
            session, document_id, limit=None, include_existing=True,
            chunk_type=chunk_type,
        )
        if force
        else None
    )
    while True:
        if forced_chunks is not None:
            chunks = forced_chunks[: batch_size * workers]
            del forced_chunks[: len(chunks)]
        else:
            chunks = get_chunks_without_embeddings_sync(
                session, document_id, limit=batch_size * workers,
                chunk_type=chunk_type,
            )
        if not chunks:
            break

        sub_batches = [chunks[i : i + batch_size] for i in range(0, len(chunks), batch_size)]
        model_name = active_embedding_model_sync()

        with ThreadPoolExecutor(max_workers=min(workers, len(sub_batches))) as pool:
            for batch, embeddings, exc in pool.map(_embed_batch, sub_batches):
                if exc is not None:
                    raise exc
                if not embeddings or len(embeddings) != len(batch):
                    raise ValueError(
                        f"Generated embedding count ({len(embeddings) if embeddings else 0}) "
                        f"does not match chunk count ({len(batch)})"
                    )
                _persist_batch(session, model_name, batch, embeddings)
                total_embedded += len(batch)

        logger.info(
            f"Embedded {total_embedded} chunks synchronously for document {document_id} "
            f"(concurrency={workers})"
        )

    return total_embedded
