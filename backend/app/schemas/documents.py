"""Document schemas."""

from typing import Optional
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    original_filename: str
    # Reader-chosen display name. None means "no override" — clients fall back
    # to original_filename, which for an arXiv download is an id, not a title.
    title: Optional[str] = None
    file_size_bytes: Optional[int] = None
    page_count: Optional[int] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    extractor: Optional[str] = None  # "mineru", "pymupdf_fallback", or "trafilatura"
    doc_kind: Optional[str] = None  # "book" (chapter navigation), "paper" (linear), or "article"
    # The page a doc_kind='article' row was imported from; None otherwise.
    source_url: Optional[str] = None
    # Fine-grained processing stage from the most-recent ingestion job
    # (queued / extracting / chunking / embedding / complete / failed).
    # Lets the library show a live progress bar without per-card /progress calls.
    job_status: Optional[str] = None
    # Real progress *within* job_status (e.g. pages extracted / total while
    # extracting). None when there's nothing finer-grained than the status.
    job_progress_fraction: Optional[float] = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


class RenameDocumentRequest(BaseModel):
    # None or blank clears the override and restores the filename.
    title: Optional[str] = None


class ImportArticleRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class DocumentUploadResponse(BaseModel):
    id: UUID
    filename: str
    status: str
    message: str

