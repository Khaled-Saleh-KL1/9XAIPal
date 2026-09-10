"""Export a user's whole library — BibTeX, Markdown (for Obsidian), Anki
flashcards, and a library CSV. Library-wide only (no per-paper variant yet,
see docs/plans/library-export.md); synchronous, since formatting a few
dozen papers/notes is fast and there is no ingestion-style job to poll.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.core.logging import get_logger
from app.database.repositories import documents as doc_repo
from app.database.repositories import notes as note_repo
from app.database.repositories import personal as personal_repo
from app.search.semantic_scholar_client import match_reference, Unresolved
from app.services.export import to_bibtex, to_markdown_zip, to_anki_tsv, to_library_csv

logger = get_logger(__name__)
router = APIRouter()


async def _resolve_pending(session: AsyncSession, documents: list[dict]) -> None:
    """Fill in resolved_authors/resolved_year for every document not yet
    permanently resolved, in place — the same lazy-resolve-on-use pattern
    GET /references/{n}/resolve already uses for a citation, just triggered
    by an export instead of a chip being opened.

    ⚠ Skips only 'resolved' and 'no_match' — both are a completed lookup.
    'unavailable' (no key, rate-limited, network error) is retried on every
    export rather than skipped: the whole point of not caching it as final
    is that adding the key, or waiting out the rate limit, should turn it
    into a real answer on the next attempt, not require some other trigger.
    Caught here after getting this exact check backwards once already in
    the sibling citation-resolve endpoint (chunks.py::resolve_reference).

    Bounded by the caller's own library, not a queue: a personal research
    library runs to dozens or low hundreds of papers, not thousands, so one
    call per unresolved paper per export is the right cost — permanently
    resolved papers (the common case after the first export) cost nothing.
    """
    for doc in documents:
        if doc.get("self_resolve_status") in ("resolved", "no_match"):
            continue
        title = (doc.get("title") or doc.get("original_filename") or "").strip()
        if not title:
            await doc_repo.save_self_resolution(session, doc["id"], status="no_match")
            doc["self_resolve_status"] = "no_match"
            continue
        result = await match_reference(title)
        if isinstance(result, Unresolved):
            await doc_repo.save_self_resolution(session, doc["id"], status=result.value)
            doc["self_resolve_status"] = result.value
            continue
        await doc_repo.save_self_resolution(
            session, doc["id"], status="resolved", authors=result.authors or None, year=result.year,
        )
        doc["self_resolve_status"] = "resolved"
        doc["resolved_authors"] = result.authors or None
        doc["resolved_year"] = result.year
    await session.commit()


def _attachment(content: str | bytes, filename: str, media_type: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/bibtex")
async def export_bibtex(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """The whole library as a .bib file. Resolves any not-yet-resolved
    paper against Semantic Scholar first (see _resolve_pending) — quality
    improves automatically once SEMANTIC_SCHOLAR_API_KEY is configured,
    with no export-side change needed."""
    documents = await doc_repo.list_all_documents_for_export(db, current_user["id"])
    await _resolve_pending(db, documents)
    return _attachment(to_bibtex(documents), "library.bib", "application/x-bibtex")


@router.get("/notes.zip")
async def export_notes_markdown(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Every paper's notes as Markdown, one file per paper, zipped."""
    documents = await doc_repo.list_all_documents_for_export(db, current_user["id"])
    qa_notes = await note_repo.list_all_notes_for_user(db, current_user["id"])
    personal_notes = await personal_repo.list_all_personal_notes_for_user(db, current_user["id"])

    notes_by_doc: dict[str, list[dict]] = {}
    for n in qa_notes:
        notes_by_doc.setdefault(str(n["document_id"]), []).append(n)
    personal_by_doc: dict[str, list[dict]] = {}
    for n in personal_notes:
        personal_by_doc.setdefault(str(n["document_id"]), []).append(n)

    zip_bytes = to_markdown_zip(documents, notes_by_doc, personal_by_doc)
    return _attachment(zip_bytes, "notes.zip", "application/zip")


@router.get("/anki.txt")
async def export_anki(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Every Q&A note as an Anki-importable tab-separated file. Personal
    notes are not included — see to_anki_tsv's own docstring."""
    qa_notes = await note_repo.list_all_notes_for_user(db, current_user["id"])
    return _attachment(to_anki_tsv(qa_notes), "flashcards.txt", "text/plain")


@router.get("/library.csv")
async def export_library_csv(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """The library index as a spreadsheet: one row per paper."""
    documents = await doc_repo.list_all_documents_for_export(db, current_user["id"])
    await _resolve_pending(db, documents)
    return _attachment(to_library_csv(documents), "library.csv", "text/csv")
