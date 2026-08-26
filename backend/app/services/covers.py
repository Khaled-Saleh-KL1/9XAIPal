"""First-page cover thumbnails for the library grid.

A library of six identical document glyphs is a list you have to read; a
library of six covers is one you recognise. The first page of a paper carries
its title, its authors, and usually its layout signature, which is most of what
"which one was that?" needs.

⚠ **Rendered lazily, on first request — not at ingestion.** Ingestion is
already the slow path a reader waits on, and a cover is worth nothing until the
library is actually looked at. The first request for a paper pays ~100ms; every
later one is a file read. Papers ingested before this existed get covers too,
for the same reason.

⚠ **The cache is keyed by document id alone.** A document's first page cannot
change — re-extraction and re-chunking rewrite the derived text, never the
source PDF — so there is no invalidation problem to solve here. Deleting the
paper deletes the cover with it.
"""

from pathlib import Path
from typing import Optional
from uuid import UUID

from app.core.logging import get_logger
from app.core.paths import assets_dir, covers_dir, documents_dir

logger = get_logger(__name__)

# Wide enough to stay sharp on a 2x display in a ~250px card, small enough that
# a 200-paper library's covers are a few megabytes rather than a few hundred.
_WIDTH_PX = 480

# JPEG, not PNG: a scanned or photographed page is continuous-tone, and PNG
# stores that at roughly 6x the size for no visible gain at thumbnail scale.
_QUALITY = 78


def cover_path(document_id: UUID | str) -> Path:
    return covers_dir() / f"{document_id}.jpg"


def _source_pdf(document_id: UUID | str, filename: Optional[str]) -> Optional[Path]:
    """The PDF to render from, preferring the per-document asset copy.

    Upload writes the same bytes twice — ``assets/<id>.pdf`` and
    ``documents/<filename>`` — and either will do. The asset copy is tried
    first because it is keyed by id, so it stays findable even when the
    document row's ``filename`` has drifted from what is on disk.
    """
    asset = assets_dir() / f"{document_id}.pdf"
    if asset.exists():
        return asset
    if filename:
        raw = documents_dir() / filename
        if raw.exists():
            return raw
    return None


def render_cover(document_id: UUID | str, filename: Optional[str]) -> Optional[Path]:
    """Return the cached cover, rendering it first if it does not exist yet.

    Returns ``None`` when there is no source PDF or the render fails. Callers
    must treat that as "no cover", never as an error: a paper whose first page
    will not rasterise is still a paper the reader can open.
    """
    out = cover_path(document_id)
    if out.exists() and out.stat().st_size > 0:
        return out

    src = _source_pdf(document_id, filename)
    if not src:
        return None

    try:
        # Imported here rather than at module scope: PyMuPDF is a heavy native
        # extension, and the API process should not pay to load it at startup
        # for a feature that may never be used in a given run.
        import fitz  # PyMuPDF

        with fitz.open(str(src)) as doc:
            if doc.page_count < 1:
                return None
            page = doc.load_page(0)
            # Derive the zoom from the page's own width so a US Letter page and
            # an A4 one come back the same number of pixels wide, instead of the
            # grid holding covers of two different sizes.
            zoom = _WIDTH_PX / max(1.0, page.rect.width)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            out.parent.mkdir(parents=True, exist_ok=True)
            pix.save(str(out), jpg_quality=_QUALITY)
        return out if out.exists() else None
    except Exception:
        logger.exception("cover render failed for %s", document_id)
        return None


def delete_cover(document_id: UUID | str) -> None:
    """Best-effort removal, called when the document is deleted."""
    try:
        cover_path(document_id).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("could not remove cover for %s: %s", document_id, e)
