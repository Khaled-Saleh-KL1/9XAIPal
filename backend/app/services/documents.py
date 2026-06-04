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
    filename: str,
    original_filename: str,
    file_size_bytes: Optional[int] = None,
    doc_kind: str = "paper",
) -> dict:
    """Create a document record."""
    return await doc_repo.create_document(
        session,
        filename=filename,
        original_filename=original_filename,
        file_size_bytes=file_size_bytes,
        doc_kind=doc_kind,
    )


async def get_document(session: AsyncSession, document_id: UUID) -> Optional[dict]:
    """Get a document by ID."""
    return await doc_repo.get_document(session, document_id)


async def list_documents(session: AsyncSession, limit: int = 50, offset: int = 0) -> list[dict]:
    """List all documents."""
    return await doc_repo.list_documents(session, limit=limit, offset=offset)


async def count_documents(session: AsyncSession) -> int:
    """Total number of documents in the library (across all pages)."""
    return await doc_repo.count_documents(session)


async def delete_document(session: AsyncSession, document_id: UUID) -> Optional[dict]:
    """Delete a document row (cascades to chunks, embeddings, assets, turns).

    Returns the deleted document's metadata so the caller can clean up
    on-disk artefacts (raw PDFs, MinerU output, extracted images). Returns
    ``None`` if the document does not exist.
    """
    doc = await doc_repo.get_document(session, document_id)
    if not doc:
        return None
    deleted = await doc_repo.delete_document(session, document_id)
    return doc if deleted else None

