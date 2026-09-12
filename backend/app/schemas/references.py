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
    # For the reader's own follow-up when the resolver cannot finish the job:
    # `search_query` is the title (resolved, or the resolver's own guess) to
    # paste into a search — never the whole citation string; `s2_url` /
    # `arxiv_url` are the landing pages when the match exists but has no
    # fetchable PDF.
    search_query: Optional[str] = None
    s2_url: Optional[str] = None
    arxiv_url: Optional[str] = None
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


class FindReferenceResponse(BaseModel):
    # The web attempt (POST …/find-web). `query` is what was searched, shown
    # either way so a miss is not a mystery; `added` is the same payload /add
    # returns, present only when a copy was found and queued.
    found: bool
    query: str
    entry: ReferenceEntry
    added: Optional[AddReferenceResponse] = None
