"""Chunk endpoints: sequential reading by sequence_order."""

import re
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.core.paths import documents_dir
from app.api.errors import ChunkNotFound, DocumentNotFound
from app.schemas.chunks import ChunkResponse, ChunkListResponse
from app.services import chunks as chunk_service
from app.services import documents as doc_service
from app.services.outline import heading_level
from app.services import book_outline
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import figure_descriptions as fig_desc_repo

router = APIRouter()


@router.get("/{paper_id}/chunks")
async def list_chunks(
    paper_id: UUID,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List chunks for a paper (paginated) and report the true total."""
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    chunks = await chunk_service.get_document_chunks(db, paper_id, limit, offset)
    total = await chunk_repo.count_document_chunks(db, paper_id)
    return {
        "chunks": chunks,
        "paper_id": str(paper_id),
        "total": total,
    }


def _shape_chunk_for_reader(chunk: dict, image_url: str | None) -> dict:
    """Shape a chunk row for the reader given its (already resolved) image URL."""
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


async def _serialize_chunk(db: AsyncSession, chunk: dict) -> dict:
    """Shape a single chunk row for the reader, attaching its image URL if any.

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

    return _shape_chunk_for_reader(chunk, image_url)


@router.get("/{paper_id}/document")
async def get_full_document(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return every block of the paper, in reading order, in ONE response.

    This is what the article reader loads. The old reader walked
    ``/chunks/after/{seq}`` once per chunk — a 105-chunk paper cost 105
    sequential round-trips before a single word appeared. Here the chunks and
    all their image assets are fetched in two queries and joined in memory.

    ``outline`` is the heading spine, which the reader uses for the section
    rail and the agent uses as its map of the document.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    chunks = await chunk_repo.get_all_document_chunks(db, paper_id)

    from app.database.repositories import assets as asset_repo
    assets = await asset_repo.get_assets_for_chunks(db, [c["id"] for c in chunks])
    by_chunk: dict = {}
    for a in assets:
        by_chunk.setdefault(a["chunk_id"], []).append(a)

    blocks = []
    outline = []
    for c in chunks:
        chunk_assets = by_chunk.get(c["id"], [])
        image_url = next(
            (
                f"/static/images/{a['file_path']}"
                for a in chunk_assets
                if a.get("asset_type") == "image" and a.get("file_path")
            ),
            None,
        )
        blocks.append({
            "id": str(c["id"]),
            "sequence_order": c["sequence_id"],
            "structural_type": c["chunk_type"],
            "content_markdown": c["markdown"],
            "plain_text": c["plain_text"],
            "heading_path": c.get("heading_path"),
            "page_start": c.get("page_start"),
            "page_end": c.get("page_end"),
            "table_json": c.get("table_json"),
            "image_url": image_url,
        })
        if c["chunk_type"] == "heading":
            text = (c.get("plain_text") or "").strip()
            outline.append({
                "sequence_order": c["sequence_id"],
                "text": text,
                # ⚠ Not len(heading_path). MinerU flattens every section to
                # depth 2, so that number cannot tell "3" from "3.1" and the
                # contents render as one flat column. See services/outline.py.
                "level": heading_level(text, len(c.get("heading_path") or [])),
            })

    return {
        "paper_id": str(paper_id),
        # A rename wins over the filename here too — the reader's title bar
        # showing "2608.09888v1" while the library shows the real name would
        # read as two different documents.
        "title": (doc.get("title") or "").strip()
                 or (doc.get("original_filename") or "").rsplit(".", 1)[0],
        "doc_kind": doc.get("doc_kind"),
        "status": doc.get("status"),
        "page_count": doc.get("page_count"),
        "extractor": doc.get("extractor"),
        "blocks": blocks,
        "outline": outline,
        "total": len(blocks),
    }


@router.get("/{paper_id}/chunks/after/{sequence_order}")
async def get_chunk_after_sequence(
    paper_id: UUID,
    sequence_order: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Fetch the next chunk whose sequence_id is strictly greater than the given one.

    The reader advances with this rather than guessing ``seq + 1`` so a gap in
    the sequence numbers (e.g. a dropped block from an older ingest) can never
    silently truncate a paper. Pass ``0`` to get the very first chunk.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    chunk = await chunk_repo.get_next_chunk(db, paper_id, sequence_order)
    if not chunk:
        raise ChunkNotFound(f"No chunk after sequence_order={sequence_order}")
    return await _serialize_chunk(db, chunk)


# ⚠ Must be registered before /{paper_id}/chunks/{sequence_order}: that route's
# path parameter has no `:int` converter, so a plain string route registered
# after it would never be reached — "range" would match {sequence_order} first
# and fail int validation with a 422 instead of hitting this route.
@router.get("/{paper_id}/chunks/range")
async def get_chunks_range(
    paper_id: UUID,
    after: int,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bulk, gap-tolerant fetch of every chunk after `after`, up to `limit`.

    Same identical bug get_full_document's docstring describes for the
    article reader: the book reader used to fast-forward to a saved position
    by calling /chunks/after/{seq} once per chunk — hundreds of sequential
    round trips to restore a deep chapter. This does it in one query, batching
    image-asset lookups the same way get_full_document does.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    limit = max(1, min(limit, 2000))
    chunks = await chunk_repo.get_chunks_after(db, paper_id, after, limit=limit)

    from app.database.repositories import assets as asset_repo
    assets = await asset_repo.get_assets_for_chunks(db, [c["id"] for c in chunks])
    by_chunk: dict = {}
    for a in assets:
        by_chunk.setdefault(a["chunk_id"], []).append(a)

    result = []
    for c in chunks:
        image_url = next(
            (
                f"/static/images/{a['file_path']}"
                for a in by_chunk.get(c["id"], [])
                if a.get("asset_type") == "image" and a.get("file_path")
            ),
            None,
        )
        result.append(_shape_chunk_for_reader(c, image_url))

    return {"chunks": result, "paper_id": str(paper_id)}


@router.get("/{paper_id}/chunks/{sequence_order}")
async def get_chunk_by_sequence(
    paper_id: UUID,
    sequence_order: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Fetch the single structural chunk at the given sequence_order."""
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    chunk = await chunk_repo.get_chunk_by_sequence(db, paper_id, sequence_order)
    if not chunk:
        raise ChunkNotFound(f"No chunk at sequence_order={sequence_order}")
    return await _serialize_chunk(db, chunk)


# A heading that is really a figure/table caption MinerU mislabelled. These
# are never chapters, and on a figure-heavy book they can outnumber the real
# ones — 6 of 18 on the book this filter was written against.
_CAPTION_HEADING = re.compile(
    r"^\s*(FIGURE|FIG\.?|TABLE|CHART|EXHIBIT|PLATE|BOX)\s*[\dIVXA-E]", re.IGNORECASE
)


def _is_plausible_chapter_heading(text: str) -> bool:
    """Reject headings that cannot be a chapter opening.

    Deliberately conservative — it only removes things that are *structurally*
    not chapters, never anything judged on topic or style:

    * figure/table captions (see `_CAPTION_HEADING`),
    * ornamental dividers with no words at all ("* * *", "---"),
    * speech attributions, which a layout extractor reads as headings because
      they sit alone on their own line ("Ben laughed:", "Dirk responded:").
    """
    t = (text or "").strip()
    if not t:
        return False
    if _CAPTION_HEADING.match(t):
        return False
    # Markdown escaping survives extraction, so strip it before deciding a
    # divider has no words: "\\* \\* \\*" is punctuation, not a title.
    if not re.search(r"[A-Za-z0-9]", t.replace("\\", "")):
        return False
    if t.endswith(":") and len(t.split()) <= 6:
        return False
    return True


@router.get("/{paper_id}/chapters")
async def list_chapters(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Chapter boundaries for the reader's "Book" mode.

    Prefers the PDF's own embedded outline (the publisher's bookmark tree) and
    falls back to deriving them from extracted headings only when the file has
    none. See `services/book_outline.py` for why that order matters — heading
    levels from a layout extractor are not a reliable chapter signal.

    Each chapter is a `[start_sequence, end_sequence]` range the reader pages
    within. `source` says which mechanism produced the list, so a surprising
    contents panel can be diagnosed without re-deriving it by hand.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    lo, hi = await chunk_repo.get_sequence_bounds(db, paper_id)
    if hi == 0:
        return {"paper_id": str(paper_id), "doc_kind": doc.get("doc_kind"),
                "source": "none", "chapters": []}

    chapters: list[dict] = []
    source = "headings"

    # ── Preferred: the PDF's embedded outline ──────────────────────────────
    filename = doc.get("filename")
    if filename:
        entries = book_outline.read_pdf_outline(documents_dir() / filename)
        if len(entries) >= 2:
            page_starts = await chunk_repo.get_page_starts(db, paper_id)
            candidate = book_outline.outline_to_chapters(
                book_outline.collapse_outline(entries), page_starts, lo, hi
            )
            # One entry is not a table of contents — it is the title bookmark of
            # a file whose outline was never filled in. Fall through to headings.
            if len(candidate) >= 2:
                chapters = candidate
                source = "pdf_outline"

    # ── Fallback: derive from extracted headings ───────────────────────────
    if not chapters:
        all_headings = [
            h for h in await chunk_repo.get_chapter_headings(db, paper_id)
            if _is_plausible_chapter_heading(h.get("plain_text") or "")
        ]

        # Pick the chapter level = the shallowest heading level that actually
        # splits the document into 2+ parts. This avoids collapsing to a single
        # "chapter" when MinerU marks the title as the only level-1 heading and
        # the real sections as level-2 (the common case for papers).
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
            chapters.append({"title": "Full document", "level": 1,
                             "start_sequence": lo, "end_sequence": hi})
        else:
            # Content before the first chapter heading = front matter / preface.
            if headings[0]["sequence_id"] > lo:
                chapters.append({
                    "title": "Front matter",
                    "level": 1,
                    "start_sequence": lo,
                    "end_sequence": headings[0]["sequence_id"] - 1,
                })
            for i, h in enumerate(headings):
                start = h["sequence_id"]
                end = headings[i + 1]["sequence_id"] - 1 if i + 1 < len(headings) else hi
                title = (h.get("plain_text") or "").strip() or f"Chapter {i + 1}"
                chapters.append({"title": title, "level": chapter_level or 1,
                                 "start_sequence": start, "end_sequence": end})

    # Front/back apparatus collapses into one entry at each end, whichever
    # mechanism produced the list — five clicks of Copyright/Dedication in
    # front of a book is noise, not navigation.
    chapters = book_outline.group_matter(chapters)

    for idx, ch in enumerate(chapters):
        ch["index"] = idx
        ch["chunk_count"] = ch["end_sequence"] - ch["start_sequence"] + 1

    return {"paper_id": str(paper_id), "doc_kind": doc.get("doc_kind"),
            "source": source, "chapters": chapters}


@router.get("/{paper_id}/figure-descriptions")
async def get_figure_descriptions(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return all rich VLM-generated figure descriptions for a paper.
    Used by the frontend for clean, high-quality rendering of architectures and diagrams.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    descriptions = await fig_desc_repo.get_figure_descriptions_for_document(db, paper_id)
    return {"descriptions": descriptions}
