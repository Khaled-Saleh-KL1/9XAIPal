"""Document service: lifecycle operations."""

import shutil
from uuid import UUID
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.paths import documents_dir
from app.database.repositories import documents as doc_repo


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
    """Create a document record."""
    return await doc_repo.create_document(
        session,
        user_id=user_id,
        filename=filename,
        original_filename=original_filename,
        file_size_bytes=file_size_bytes,
        doc_kind=doc_kind,
        source_url=source_url,
    )


async def get_document(session: AsyncSession, document_id: UUID, user_id: UUID) -> Optional[dict]:
    """Get a document by ID, scoped to its owner."""
    return await doc_repo.get_document(session, document_id, user_id)


async def list_documents(
    session: AsyncSession, user_id: UUID, limit: int = 50, offset: int = 0
) -> list[dict]:
    """List this user's documents."""
    return await doc_repo.list_documents(session, user_id, limit=limit, offset=offset)


async def count_documents(session: AsyncSession, user_id: UUID) -> int:
    """Total number of documents this user owns (across all pages)."""
    return await doc_repo.count_documents(session, user_id)


async def rename_document(
    session: AsyncSession, document_id: UUID, user_id: UUID, title: Optional[str]
) -> Optional[dict]:
    """Rename a document and return its fresh row, or None if it is gone (or not owned).

    An empty or whitespace-only title clears the override rather than storing
    a blank one: a paper whose name renders as nothing is worse than one still
    called by its filename.
    """
    clean = (title or "").strip() or None
    if not await doc_repo.set_document_title(session, document_id, user_id, clean):
        return None
    return await doc_repo.get_document(session, document_id, user_id)


async def delete_document(session: AsyncSession, document_id: UUID, user_id: UUID) -> Optional[dict]:
    """Delete a document row (cascades to chunks, embeddings, assets, turns).

    Returns the deleted document's metadata so the caller can clean up
    on-disk artefacts (raw PDFs, MinerU output, extracted images). Returns
    ``None`` if the document does not exist (or isn't owned by this user).
    """
    doc = await doc_repo.get_document(session, document_id, user_id)
    if not doc:
        return None
    deleted = await doc_repo.delete_document(session, document_id, user_id)
    return doc if deleted else None
