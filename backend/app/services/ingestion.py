"""Ingestion service: transactional document + chunk persistence."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import documents as doc_repo
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import assets as asset_repo
from app.core.logging import get_logger

logger = get_logger(__name__)


async def create_ingestion_job(session: AsyncSession, document_id: UUID) -> dict:
    """Create a new ingestion job."""
    result = await session.execute(
        text("""
            INSERT INTO ingestion_jobs (document_id, status)
            VALUES (:document_id, 'queued')
            RETURNING id, document_id, status, created_at
        """),
        {"document_id": document_id},
    )
    return dict(result.mappings().one())


async def update_job_status(
    session: AsyncSession,
    job_id: UUID,
    status: str,
    *,
    error_message: Optional[str] = None,
) -> None:
    """Update ingestion job status."""
    sets = ["status = :status"]
    params: dict = {"id": job_id, "status": status}

    if status in ("extracting", "chunking", "embedding") and error_message is None:
        sets.append("started_at = COALESCE(started_at, NOW())")
    if status in ("complete", "failed"):
        sets.append("completed_at = NOW()")
    if error_message:
        sets.append("error_message = :error")
        params["error"] = error_message

    await session.execute(
        text(f"UPDATE ingestion_jobs SET {', '.join(sets)} WHERE id = :id"),
        params,
    )


async def store_chunks(
    session: AsyncSession,
    document_id: UUID,
    chunks: list[dict],
) -> list[dict]:
    """Store ordered chunks for a document transactionally.

    Strips non-persisted helper fields (e.g. ``image_refs``) before insertion.
    """
    _PERSIST_FIELDS = {
        "document_id", "sequence_id", "parent_sequence_id", "chunk_type",
        "heading_path", "markdown", "plain_text", "page_start", "page_end",
        "bbox_json", "token_count", "table_json",
    }
    payload = []
    for chunk in chunks:
        chunk["document_id"] = document_id
        payload.append({k: v for k, v in chunk.items() if k in _PERSIST_FIELDS})
    return await chunk_repo.create_chunks(session, payload)


async def mark_document_complete(
    session: AsyncSession,
    document_id: UUID,
    page_count: Optional[int] = None,
) -> None:
    """Mark document as complete."""
    await doc_repo.update_document_status(
        session, document_id, "complete", page_count=page_count
    )
    logger.info(f"Document {document_id} marked complete")


async def mark_document_failed(
    session: AsyncSession,
    document_id: UUID,
    error_message: str,
) -> None:
    """Mark document as failed."""
    await doc_repo.update_document_status(
        session, document_id, "failed", error_message=error_message
    )
    logger.error(f"Document {document_id} failed: {error_message}")

