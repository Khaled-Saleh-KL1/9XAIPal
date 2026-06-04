"""Document repository: CRUD for document metadata."""

from uuid import UUID
from typing import Optional, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_document(
    session: AsyncSession,
    *,
    filename: str,
    original_filename: str,
    file_size_bytes: Optional[int] = None,
    doc_kind: str = "paper",
) -> dict:
    """Insert a new document record."""
    result = await session.execute(
        text("""
            INSERT INTO documents (filename, original_filename, file_size_bytes, doc_kind)
            VALUES (:filename, :original_filename, :file_size_bytes, :doc_kind)
            RETURNING id, filename, original_filename, file_size_bytes, doc_kind, status, created_at
        """),
        {
            "filename": filename,
            "original_filename": original_filename,
            "file_size_bytes": file_size_bytes,
            "doc_kind": doc_kind if doc_kind in ("book", "paper") else "paper",
        },
    )
    return dict(result.mappings().one())


async def get_document(session: AsyncSession, document_id: UUID) -> Optional[dict]:
    """Fetch a document by ID."""
    result = await session.execute(
        text("SELECT * FROM documents WHERE id = :id"),
        {"id": document_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def list_documents(session: AsyncSession, limit: int = 50, offset: int = 0) -> list[dict]:
    """List all documents ordered by creation date.

    Also attaches the *most-recent* ingestion job's status as ``job_status`` so
    the library UI can render a live progress bar (extracting / chunking /
    embedding) without an N+1 poll-per-card.
    """
    result = await session.execute(
        text("""
            SELECT d.*, j.status AS job_status
            FROM documents d
            LEFT JOIN LATERAL (
                SELECT status
                FROM ingestion_jobs
                WHERE document_id = d.id
                ORDER BY created_at DESC
                LIMIT 1
            ) j ON TRUE
            ORDER BY d.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset},
    )
    return [dict(r) for r in result.mappings().all()]


async def count_documents(session: AsyncSession) -> int:
    """Return the total number of documents in the library."""
    result = await session.execute(text("SELECT COUNT(*) AS n FROM documents"))
    row = result.mappings().first()
    return int(row["n"]) if row else 0


async def update_document_status(
    session: AsyncSession,
    document_id: UUID,
    status: str,
    *,
    error_message: Optional[str] = None,
    page_count: Optional[int] = None,
) -> None:
    """Update document ingestion status."""
    sets = ["status = :status", "updated_at = NOW()"]
    params: dict = {"id": document_id, "status": status}

    if error_message is not None:
        sets.append("error_message = :error_message")
        params["error_message"] = error_message
    if page_count is not None:
        sets.append("page_count = :page_count")
        params["page_count"] = page_count

    await session.execute(
        text(f"UPDATE documents SET {', '.join(sets)} WHERE id = :id"),
        params,
    )


async def delete_document(session: AsyncSession, document_id: UUID) -> bool:
    """Delete a document (cascades to chunks, embeddings, assets)."""
    result = cast(
        CursorResult[tuple[()]],
        await session.execute(
            text("DELETE FROM documents WHERE id = :id"),
            {"id": document_id},
        ),
    )
    return (result.rowcount or 0) > 0

