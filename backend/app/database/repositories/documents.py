"""Document repository: CRUD for document metadata."""

from uuid import UUID
from typing import Optional, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession


VALID_DOC_KINDS = ("book", "paper", "article")


async def create_document(
    session: AsyncSession,
    *,
    user_id: UUID,
    filename: str,
    original_filename: str,
    file_size_bytes: Optional[int] = None,
    doc_kind: str = "paper",
    source_url: Optional[str] = None,
) -> dict:
    """Insert a new document record.

    user_id is a required, non-Optional argument (not enforced at the DB
    level — see the column comment in schema.sql) so a missing owner is a
    TypeError at call time, not a silently-NULL row discovered later.

    source_url is only ever set for doc_kind='article' (an imported web
    page) — NULL for anything uploaded as a file.
    """
    result = await session.execute(
        text("""
            INSERT INTO documents (user_id, filename, original_filename, file_size_bytes, doc_kind, source_url)
            VALUES (:user_id, :filename, :original_filename, :file_size_bytes, :doc_kind, :source_url)
            RETURNING id, filename, original_filename, file_size_bytes, doc_kind, source_url, status, created_at
        """),
        {
            "user_id": user_id,
            "filename": filename,
            "original_filename": original_filename,
            "file_size_bytes": file_size_bytes,
            "doc_kind": doc_kind if doc_kind in VALID_DOC_KINDS else "paper",
            "source_url": source_url,
        },
    )
    return dict(result.mappings().one())


async def get_document(session: AsyncSession, document_id: UUID, user_id: UUID) -> Optional[dict]:
    """Fetch a document by ID, scoped to its owner.

    Returns None for both "no such document" and "exists but belongs to
    someone else" — callers 404 either way, which is deliberate: a 403 would
    confirm the document exists, a 404 doesn't (see docs on the retrofit).
    """
    result = await session.execute(
        text("SELECT * FROM documents WHERE id = :id AND user_id = :user_id"),
        {"id": document_id, "user_id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def list_documents(
    session: AsyncSession, user_id: UUID, limit: int = 50, offset: int = 0
) -> list[dict]:
    """List this user's documents ordered by creation date.

    Also attaches the *most-recent* ingestion job's status as ``job_status``,
    and its ``progress_fraction`` (real progress within that status, e.g.
    pages extracted / total while extracting — None when nothing finer than
    the status is available), so the library UI can render a live, honest
    progress bar without an N+1 poll-per-card. Same reasoning for
    ``raw_page_count`` (see raw_snapshot_status's own column comment in
    schema.sql) — RawFilesPanel needs it per-row without an extra request.
    """
    result = await session.execute(
        text("""
            SELECT d.*, j.status AS job_status, j.progress_fraction AS job_progress_fraction,
                   COALESCE(r.raw_page_count, 0) AS raw_page_count
            FROM documents d
            LEFT JOIN LATERAL (
                SELECT status, progress_fraction
                FROM ingestion_jobs
                WHERE document_id = d.id
                ORDER BY created_at DESC
                LIMIT 1
            ) j ON TRUE
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS raw_page_count
                FROM raw_snapshot_pages
                WHERE document_id = d.id
            ) r ON TRUE
            WHERE d.user_id = :user_id
            ORDER BY d.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"user_id": user_id, "limit": limit, "offset": offset},
    )
    return [dict(r) for r in result.mappings().all()]


async def count_documents(session: AsyncSession, user_id: UUID) -> int:
    """Return the total number of documents this user owns."""
    result = await session.execute(
        text("SELECT COUNT(*) AS n FROM documents WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
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
    """Update document ingestion status.

    ⚠ Deliberately NOT user-scoped. This is called only from trusted internal
    code (the Celery ingestion pipeline) against a document_id that was
    already validated as owned at upload time — Celery tasks have no
    request-scoped "current user" to check against.
    """
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


async def set_document_title(
    session: AsyncSession, document_id: UUID, user_id: UUID, title: Optional[str]
) -> bool:
    """Set (or clear) a document's display title, scoped to its owner.

    ``None`` clears the override so the UI falls back to original_filename.
    Returns False when no such document exists (or it belongs to someone
    else), so the endpoint 404s rather than silently reporting a rename that
    touched nothing.
    """
    result = cast(
        CursorResult[tuple[()]],
        await session.execute(
            text("""
                UPDATE documents
                SET title = :title, updated_at = NOW()
                WHERE id = :id AND user_id = :user_id
            """),
            {"id": document_id, "user_id": user_id, "title": title},
        ),
    )
    return (result.rowcount or 0) > 0


async def filter_owned_document_ids(
    session: AsyncSession, document_ids: list[UUID], user_id: UUID
) -> list[UUID]:
    """Narrow a client-supplied list of document ids down to the ones this
    user actually owns, silently dropping the rest.

    Used anywhere a request names documents by id without loading each one
    individually (study membership, sticky-note paper references) — without
    this, a study or sticky could be made to reference another user's
    document, and its content would then leak through that study's/sticky's
    own (correctly user-scoped) chat.
    """
    if not document_ids:
        return []
    result = await session.execute(
        text("SELECT id FROM documents WHERE id = ANY(:ids) AND user_id = :user_id"),
        {"ids": list(document_ids), "user_id": user_id},
    )
    return [row[0] for row in result.fetchall()]


async def delete_document(session: AsyncSession, document_id: UUID, user_id: UUID) -> bool:
    """Delete a document (cascades to chunks, embeddings, assets), scoped to its owner."""
    result = cast(
        CursorResult[tuple[()]],
        await session.execute(
            text("DELETE FROM documents WHERE id = :id AND user_id = :user_id"),
            {"id": document_id, "user_id": user_id},
        ),
    )
    return (result.rowcount or 0) > 0
