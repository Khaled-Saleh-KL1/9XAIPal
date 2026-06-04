"""Chunk endpoints: sequential reading by sequence_order."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import ChunkNotFound, DocumentNotFound
from app.schemas.chunks import ChunkResponse, ChunkListResponse
from app.services import chunks as chunk_service
from app.services import documents as doc_service
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import figure_descriptions as fig_desc_repo

router = APIRouter()


@router.get("/{paper_id}/chunks")
async def list_chunks(
    paper_id: UUID,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List chunks for a paper (paginated) and report the true total."""
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    chunks = await chunk_service.get_document_chunks(db, paper_id, limit, offset)
    total = await chunk_repo.count_document_chunks(db, paper_id)
    return {
        "chunks": chunks,
        "paper_id": str(paper_id),
        "total": total,
    }


async def _serialize_chunk(db: AsyncSession, chunk: dict) -> dict:
    """Shape a chunk row for the reader, attaching its image URL if any.

    file_path is stored relative to images_dir() (e.g. "<doc_id>/<uuid>.png"),
    which is mounted at /static/images.
    """
    from app.database.repositories import assets as asset_repo
    assets = await asset_repo.get_assets_for_chunk(db, chunk["id"])
    image_url = None
    if assets:
        for a in assets:
            if a.get("asset_type") == "image":
                image_url = f"/static/images/{a['file_path']}"
                break

    return {
        "id": str(chunk["id"]),
        "paper_id": str(chunk["document_id"]),
        "sequence_order": chunk["sequence_id"],
        "content_markdown": chunk["markdown"],
        "structural_type": chunk["chunk_type"],
        "plain_text": chunk["plain_text"],
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "heading_path": chunk.get("heading_path"),
        "image_url": image_url,
        "image_refs": chunk.get("image_refs") or [],
    }


@router.get("/{paper_id}/chunks/after/{sequence_order}")
async def get_chunk_after_sequence(
    paper_id: UUID,
    sequence_order: int,
    db: AsyncSession = Depends(get_db),
):
    """Fetch the next chunk whose sequence_id is strictly greater than the given one.

    The reader advances with this rather than guessing ``seq + 1`` so a gap in
    the sequence numbers (e.g. a dropped block from an older ingest) can never
    silently truncate a paper. Pass ``0`` to get the very first chunk.
    """
    chunk = await chunk_repo.get_next_chunk(db, paper_id, sequence_order)
    if not chunk:
        raise ChunkNotFound(f"No chunk after sequence_order={sequence_order}")
    return await _serialize_chunk(db, chunk)


@router.get("/{paper_id}/chunks/{sequence_order}")
async def get_chunk_by_sequence(
    paper_id: UUID,
    sequence_order: int,
    db: AsyncSession = Depends(get_db),
):
    """Fetch the single structural chunk at the given sequence_order."""
    chunk = await chunk_repo.get_chunk_by_sequence(db, paper_id, sequence_order)
    if not chunk:
        raise ChunkNotFound(f"No chunk at sequence_order={sequence_order}")
    return await _serialize_chunk(db, chunk)


@router.get("/{paper_id}/chapters")
async def list_chapters(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Derive chapter boundaries from the document's top-level headings.

    Used by the reader's "Book" mode so the user can jump to a chapter
    (incl. the front matter / introduction) instead of paging the whole book
    linearly. Each chapter is a sequence range [start_sequence, end_sequence]
    that the reader pages within.
    """
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    all_headings = await chunk_repo.get_chapter_headings(db, paper_id)
    lo, hi = await chunk_repo.get_sequence_bounds(db, paper_id)

    chapters: list[dict] = []
    if hi == 0:
        return {"paper_id": str(paper_id), "doc_kind": doc.get("doc_kind"), "chapters": []}

    # Pick the chapter level = the shallowest heading level that actually splits
    # the document into 2+ parts. This avoids collapsing to a single "chapter"
    # when MinerU marks the title as the only level-1 heading and the real
    # sections as level-2 (the common case for papers and many books).
    from collections import Counter
    level_counts = Counter(h["level"] for h in all_headings if h.get("level"))
    chapter_level = None
    for lvl in sorted(level_counts):
        if level_counts[lvl] >= 2:
            chapter_level = lvl
            break
    if chapter_level is None and level_counts:
        chapter_level = min(level_counts)  # only single headings exist; use shallowest

    headings = [h for h in all_headings if h.get("level") == chapter_level] if chapter_level else []

    if not headings:
        # No usable headings — present the whole document as one chapter.
        chapters.append({"title": "Full document", "start_sequence": lo, "end_sequence": hi})
    else:
        # Content before the first chapter heading = front matter / preface.
        if headings[0]["sequence_id"] > lo:
            chapters.append({
                "title": "Front matter",
                "start_sequence": lo,
                "end_sequence": headings[0]["sequence_id"] - 1,
            })
        for i, h in enumerate(headings):
            start = h["sequence_id"]
            end = headings[i + 1]["sequence_id"] - 1 if i + 1 < len(headings) else hi
            title = (h.get("plain_text") or "").strip() or f"Chapter {i + 1}"
            chapters.append({"title": title, "start_sequence": start, "end_sequence": end})

    for idx, ch in enumerate(chapters):
        ch["index"] = idx
        ch["chunk_count"] = ch["end_sequence"] - ch["start_sequence"] + 1

    return {"paper_id": str(paper_id), "doc_kind": doc.get("doc_kind"), "chapters": chapters}


@router.get("/{paper_id}/figure-descriptions")
async def get_figure_descriptions(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return all rich VLM-generated figure descriptions for a paper.
    Used by the frontend for clean, high-quality rendering of architectures and diagrams.
    """
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    descriptions = await fig_desc_repo.get_figure_descriptions_for_document(db, paper_id)
    return {"descriptions": descriptions}
