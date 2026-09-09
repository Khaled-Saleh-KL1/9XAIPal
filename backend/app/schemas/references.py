"""Schemas for a paper's own bibliography — clickable in-body citations.

See services/references.py (parsing), database/repositories/paper_references.py
(storage), search/semantic_scholar_client.py (resolution), and
api/v1/endpoints/chunks.py (the three endpoints these back).
"""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ReferenceEntry(BaseModel):
    number: int
    raw_text: str
    resolve_status: str  # pending | resolved | no_match | unavailable
    resolved_title: Optional[str] = None
    resolved_authors: Optional[str] = None
    resolved_year: Optional[int] = None
    # Present only once resolved; the /add endpoint re-validates this
    # server-side rather than trusting whatever URL a client might send.
    resolved_pdf_url: Optional[str] = None
    already_in_library: bool = False
    # Set only when already_in_library — lets the frontend link straight to
    # the existing copy instead of offering "Add" again.
    existing_document_id: Optional[UUID] = None


class ReferenceListResponse(BaseModel):
    references: list[ReferenceEntry]
    document_id: UUID


class AddReferenceResponse(BaseModel):
    id: UUID
    filename: str
    status: str
    message: str
    already_existed: bool = False
