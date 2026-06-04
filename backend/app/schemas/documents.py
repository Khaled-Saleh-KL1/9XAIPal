"""Document schemas."""

from typing import Optional
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    original_filename: str
    file_size_bytes: Optional[int] = None
    page_count: Optional[int] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    extractor: Optional[str] = None  # "mineru" or "pymupdf_fallback"
    doc_kind: Optional[str] = None  # "book" (chapter navigation) or "paper" (linear)
    # Fine-grained processing stage from the most-recent ingestion job
    # (queued / extracting / chunking / embedding / complete / failed).
    # Lets the library show a live progress bar without per-card /progress calls.
    job_status: Optional[str] = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


class DocumentUploadResponse(BaseModel):
    id: UUID
    filename: str
    status: str
    message: str

