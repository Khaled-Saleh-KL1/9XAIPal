"""PDF structural extraction via MinerU with PyMuPDF degraded fallback.

Primary path: invokes the host-installed ``mineru`` CLI (3.x) with the
``pipeline`` backend in ``auto`` method, which runs layout detection, OCR for
scanned/image-only pages, table structure recognition, and equation → LaTeX.
Produces:

    <output>/<stem>/auto/
        <stem>.md                    structured markdown with $...$ math, | ... | tables, ![](img)
        <stem>_content_list.json     ordered typed blocks with page indices
        images/*.jpg                 figures + table images

We surface the markdown for storage and the content_list.json for the chunker,
which produces typed chunks with real page numbers.

Degraded fallback: when ``settings.allow_pymupdf_fallback`` is true AND mineru
is missing, we fall back to a PyMuPDF text-only extractor. This is fast but
produces no OCR, no table recognition, and no math LaTeX — every section ends up
as a ``text`` chunk. The fallback logs a loud warning so failures are not silent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF (only used by the degraded fallback)

from app.core.config import settings
from app.core.logging import get_logger
from app.core.paths import extracted_dir

logger = get_logger(__name__)


class MinerUError(Exception):
    """Raised when PDF extraction fails."""


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


EXTRACTOR_MINERU = "mineru"
EXTRACTOR_PYMUPDF = "pymupdf_fallback"


def extract_pdf_sync(pdf_path: Path, document_id: str) -> tuple[Path, str]:
    """Extract a PDF using MinerU synchronously, with PyMuPDF fallback on failure.

    Returns ``(output_dir, extractor_name)``. ``extractor_name`` is one of
    ``EXTRACTOR_MINERU`` or ``EXTRACTOR_PYMUPDF`` so callers (and the UI)
    can show which path produced the chunks.

    Fallback is taken when:
      * mineru binary is missing on PATH, OR
      * mineru runs but fails (non-zero exit, timeout, produces no markdown)
    AND ``settings.allow_pymupdf_fallback`` is true. Otherwise raises.
    """
    output_dir = extracted_dir() / document_id
    output_dir.mkdir(parents=True, exist_ok=True)
    allow_fallback = bool(getattr(settings, "allow_pymupdf_fallback", False))

    binary = settings.mineru_binary
    if shutil.which(binary) is None:
        if allow_fallback:
            logger.warning(
                "mineru binary not found on PATH — falling back to PyMuPDF "
                "text-only extractor. Install MinerU for higher fidelity."
            )
            return _extract_with_pymupdf(pdf_path, output_dir), EXTRACTOR_PYMUPDF
        raise MinerUError(
            f"'{binary}' not found on PATH. Install MinerU "
            "(`pip install -U 'mineru[pipeline]'` then `mineru-models-download "
            "-s huggingface -m pipeline`), or set ALLOW_PYMUPDF_FALLBACK=true "
            "to use the degraded text-only extractor."
        )

    cmd = [
        binary,
        "-p", str(pdf_path),
        "-o", str(output_dir),
        "-m", "auto",
        "-b", "pipeline",
        "-l", settings.mineru_lang,
    ]
    logger.info(f"Running MinerU: {' '.join(cmd)}")

    # Local-first robustness: once MinerU's models are cached, the HuggingFace
    # hub still makes a network "is this up to date?" call on every run. A
    # transient network blip turns that into `All connection attempts failed`
    # and aborts the whole extraction (this is exactly what killed a re-upload
    # here). So we run MinerU fully offline whenever the model cache already
    # exists — the cached weights are used directly and no network is touched.
    # Behaviour: online only on a fresh install (cache absent) so models can
    # download the first time. Override with MINERU_FORCE_OFFLINE=1 (always
    # offline) or MINERU_FORCE_OFFLINE=0 (always allow network).
    env = os.environ.copy()
    force_offline = os.getenv("MINERU_FORCE_OFFLINE")
    hf_home = Path(os.getenv("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    if force_offline == "1" or (force_offline != "0" and hf_home.exists()):
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        logger.info("MinerU running offline (model cache present); network disabled for extraction.")

    # Memory safety: MinerU processes pages in windows (default 64) and runs
    # formula recognition (MFR) on a whole window's equations at once. On a
    # formula-dense book inside Docker's memory-capped VM that peak gets
    # OOM-killed ("Server disconnected without sending a response" mid-MFR).
    # A smaller window bounds peak RAM — slightly more model passes, far lower
    # memory. Override with MINERU_PROCESSING_WINDOW_SIZE in the environment.
    env.setdefault("MINERU_PROCESSING_WINDOW_SIZE", "16")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.mineru_timeout_sec,
            env=env,
        )
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "")[-1500:]
            raise MinerUError(f"mineru failed (exit {proc.returncode}): {stderr_tail}")
        md = find_markdown_output(output_dir)
        if md is None:
            raise MinerUError(f"mineru finished but produced no markdown under {output_dir}")
        logger.info(f"MinerU complete: {md.relative_to(output_dir)}")
        return output_dir, EXTRACTOR_MINERU

    except (subprocess.TimeoutExpired, MinerUError, FileNotFoundError, OSError) as e:
        if allow_fallback:
            logger.warning(
                f"MinerU failed ({type(e).__name__}: {e}); falling back to PyMuPDF "
                "text-only extractor for this document."
            )
            # Wipe any partial MinerU output before fallback so the directory
            # is clean and the fallback's `<stem>.md` is unambiguously the
            # active artifact.
            _wipe_dir_contents(output_dir)
            return _extract_with_pymupdf(pdf_path, output_dir), EXTRACTOR_PYMUPDF
        if isinstance(e, MinerUError):
            raise
        raise MinerUError(f"mineru invocation failed: {e}") from e


async def extract_pdf(pdf_path: Path, document_id: str) -> tuple[Path, str]:
    return extract_pdf_sync(pdf_path, document_id)


def _wipe_dir_contents(d: Path) -> None:
    """Delete files/subdirs inside ``d`` without removing ``d`` itself."""
    for child in d.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except Exception as e:
            logger.warning(f"Failed to wipe {child}: {e}")


def find_markdown_output(output_dir: Path) -> Optional[Path]:
    """Locate the primary markdown file produced by MinerU.

    MinerU writes to ``<output>/<stem>/auto/<stem>.md``. Earlier versions
    nested differently, so we scan and pick the largest .md.
    """
    md_files = list(output_dir.rglob("*.md"))
    if not md_files:
        return None
    return max(md_files, key=lambda f: f.stat().st_size)


def find_content_list(output_dir: Path) -> Optional[Path]:
    """Locate MinerU's content_list.json (typed, ordered, page-indexed blocks)."""
    candidates = list(output_dir.rglob("*_content_list.json"))
    if not candidates:
        candidates = list(output_dir.rglob("content_list.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_size)


def find_images(output_dir: Path) -> list[Path]:
    """Find all extracted figure/table images in MinerU's output."""
    exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    return [p for p in output_dir.rglob("*") if p.suffix.lower() in exts]


def get_page_count(pdf_path: Path) -> Optional[int]:
    """Get page count using PyMuPDF (cheap, doesn't need MinerU)."""
    try:
        doc = fitz.open(str(pdf_path))
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Degraded fallback: PyMuPDF text-only extraction
# ─────────────────────────────────────────────────────────────────────────────


def _extract_with_pymupdf(pdf_path: Path, output_dir: Path) -> Path:
    """Text + visual fallback.

    Used ONLY when MinerU isn't installed and the operator has explicitly opted
    into the degraded path via ALLOW_PYMUPDF_FALLBACK=true.

    For each page we emit, in reading order:
      * heading / text / math lines (math detected by symbol density)
      * markdown ``![](filename)`` references for every embedded raster image
        (those become ``figure`` chunks linked to a real asset)
      * a full-page snapshot PNG appended at the end of the page (also a
        ``figure`` chunk) so vector diagrams, tables, and equation layouts
        survive — the multimodal model can then see them in LOCAL context.
    """
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        raise MinerUError(f"Cannot open PDF: {e}")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise MinerUError("PDF has no pages")

    # First pass: collect every line + every embedded image, with their bboxes,
    # so we can sort by vertical position and interleave them. Computing the
    # body font size from ALL pages avoids per-page miscalibration on short
    # pages (references, appendices) that have very few body lines.
    body_size_global = _global_body_size(doc)

    parts: list[str] = []
    for page_num in range(total_pages):
        page = doc[page_num]
        page_md = _pymupdf_page_markdown(page, page_num, images_dir, body_size_global)
        page_snapshot = _render_page_snapshot(page, page_num, images_dir)
        if page_md:
            parts.append(page_md)
        if page_snapshot:
            parts.append(f"![Page {page_num + 1}]({page_snapshot})")
    doc.close()

    md_path = output_dir / f"{pdf_path.stem}.md"
    md_path.write_text("\n\n".join(parts), encoding="utf-8")
    return output_dir


# Characters that strongly suggest a math expression (Greek letters + symbols
# commonly produced by PDF math renderers). Subscript / superscript Unicode
# blocks too. Tuned to flag the Transformer paper's body equations while not
# tripping on prose containing one stray "·" or "α".
_MATH_GLYPHS = set(
    "αβγδεζηθικλμνξοπρςστυφχψωΑΒΓΔΕΘΛΞΠΣΦΨΩ"  # Greek
    "∑∏∫√∞≈≠≤≥≪≫⋅·∂∇·×÷±∓⊕⊗⊙∅⌊⌋⌈⌉←→↔⇒⇔∈∉⊂⊆⊃⊇∪∩∀∃ℝℕℤℚℂ"
    "²³⁴⁵⁶⁷⁸⁹⁰¹⁺⁻ⁿ"
    "₀₁₂₃₄₅₆₇₈₉₊₋"
)


def _math_score(text: str) -> float:
    stripped = text.strip()
    if len(stripped) < 3:
        return 0.0
    glyph_hits = sum(1 for c in stripped if c in _MATH_GLYPHS)
    # An "=" or "(...)" surrounded by short tokens is also math-shaped.
    eq_hits = stripped.count("=") + stripped.count("≈")
    return (glyph_hits + 0.3 * eq_hits) / max(len(stripped), 1)


def _is_math_line(text: str, is_heading: bool) -> bool:
    if is_heading:
        return False
    score = _math_score(text)
    # Body prose lands around 0.0–0.01; equation lines often hit 0.05+.
    return score >= 0.04


def _pymupdf_page_markdown(
    page: "fitz.Page",
    page_num: int,
    images_dir: Path,
    body_size: float,
) -> str:
    """Produce markdown for one page using reading-order, multi-column,
    multi-line-equation, and bottom-of-page footnote heuristics.

    Improvements over the naive y0-sorted line dump:
      * Two-column detection (left col fully emitted before right col).
      * Consecutive math-y lines fused into a single ``$$..$$`` block.
      * Lines in the bottom ~15% of the page with small font (or starting
        with a footnote marker) emitted as ``∗ <text>`` so the markdown
        chunker's footnote heuristic tags them as side notes.
    """
    # Text lines with bboxes
    text_items: list[dict] = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    blocks = text_dict.get("blocks", []) if isinstance(text_dict, dict) else []  # type: ignore[attr-defined]
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != 0:
            continue
        for line in block.get("lines", []) or []:
            if not isinstance(line, dict):
                continue
            spans = line.get("spans", []) or []
            line_text = _join_spans_with_spaces(spans).strip()
            if not line_text:
                continue
            max_size = 0.0
            is_bold = False
            for span in spans:
                if span.get("text", "").strip():
                    max_size = max(max_size, span.get("size", 0.0))
                    if "bold" in span.get("font", "").lower() or span.get("flags", 0) & 2**4:
                        is_bold = True
            bbox = line.get("bbox") or (0.0, 0.0, 0.0, 0.0)
            text_items.append({
                "kind": "text",
                "x0": bbox[0],
                "y0": bbox[1],
                "y1": bbox[3],
                "text": line_text,
                "font_size": max_size,
                "is_bold": is_bold,
            })

    # Embedded raster images on this page — save and emit ![]() refs.
    image_items = _pymupdf_extract_images(page, page_num, images_dir)
    for img in image_items:
        img.setdefault("x0", 0.0)

    page_rect = page.rect
    page_height = float(page_rect.height) or 1.0
    page_width = float(page_rect.width) or 1.0
    footnote_y_threshold = page_height * 0.85  # bottom 15%

    # Tag every text line with whether it sits in the footnote band.
    for it in text_items:
        is_small = it["font_size"] and it["font_size"] < body_size * 0.95
        in_band = it["y0"] >= footnote_y_threshold
        starts_with_marker = bool(re.match(r"^\s*[*∗⋆★✱✲†‡§¶¹²³⁰⁴-⁹]", it["text"]))
        it["is_footnote"] = bool((in_band and (is_small or starts_with_marker)) or
                                 (starts_with_marker and it["y0"] >= page_height * 0.7))

    # Two-column detection: a paper is two-column when most non-footnote body
    # lines fall cleanly into one of two horizontal bands. We test by seeing
    # if there are line starts on BOTH the left half and the right half AND a
    # large enough x-gap exists. The mid-page gutter is at page_width * 0.5
    # (with a tolerance band).
    body_lines = [it for it in text_items if not it["is_footnote"]]
    is_two_col = _detect_two_column(body_lines, page_width)

    if is_two_col:
        gutter = page_width * 0.5
        def col_key(it):
            # Left column has x0 < gutter; right column has x0 >= gutter.
            # Within a column sort by y0; columns themselves sort left→right.
            col = 0 if it["x0"] < gutter else 1
            return (col, it["y0"])
        body_lines.sort(key=col_key)
    else:
        body_lines.sort(key=lambda it: it["y0"])

    footnote_lines = [it for it in text_items if it["is_footnote"]]
    footnote_lines.sort(key=lambda it: (it["y0"], it["x0"]))

    # Interleave images with body_lines by vertical position (images don't
    # belong to a column the way text does, but ordering by y0 is good enough
    # to keep figures near where the text references them).
    body_combined = sorted(body_lines + list(image_items), key=lambda it: (it.get("y0", 0.0), it.get("x0", 0.0)))

    # Now emit markdown, fusing consecutive math-y lines into one $$ block.
    md_parts: list[str] = []
    math_buf: list[str] = []

    def flush_math():
        if math_buf:
            md_parts.append("\n$$\n" + " \\\\ \n".join(math_buf) + "\n$$\n")
            math_buf.clear()

    for it in body_combined:
        if it.get("kind") == "image":
            flush_math()
            md_parts.append(f"\n![{it['filename']}]({it['filename']})\n")
            continue
        text = it["text"]
        level = _pymupdf_heading_level(it["font_size"], body_size, text, it["is_bold"])
        if level:
            flush_math()
            md_parts.append(f"\n{'#' * level} {text}\n")
        elif _is_math_line(text, is_heading=False):
            math_buf.append(text)
        else:
            flush_math()
            md_parts.append(text)
    flush_math()

    # Footnotes go at the end of the page so the markdown chunker's footnote
    # heuristic finds them in their own paragraphs. Prefix with the ASCII
    # marker so the chunker keyword fallback flags them too.
    for fn in footnote_lines:
        text = fn["text"]
        if not re.match(r"^\s*[*∗⋆★✱✲†‡§¶]", text):
            text = f"∗ {text}"
        md_parts.append(f"\n{text}\n")

    return "\n".join(md_parts)


def _detect_two_column(lines: list[dict], page_width: float) -> bool:
    """Decide whether this page's body text is two-column.

    Returns true when both halves of the page have substantial line counts
    AND there is a vertical strip near the page midline that very few lines
    cross. Tuned to NOT misfire on single-column ICML/CVPR-style papers.
    """
    if len(lines) < 12:
        return False
    mid = page_width * 0.5
    left = sum(1 for it in lines if it["x0"] < mid - 4)
    right = sum(1 for it in lines if it["x0"] >= mid + 4)
    if min(left, right) < max(4, len(lines) * 0.2):
        return False
    # Lines whose horizontal extent straddles the gutter would break a clean
    # two-column read; require <20% of lines to cross.
    crossing = 0
    for it in lines:
        # A line "crosses" the gutter when its bbox spans both halves of the page.
        # We don't have x1 for text lines here (we kept x0 only), so approximate
        # with: a line whose x0 is in the *left half* but extends to the right
        # half is identified by its text being unusually long for one column.
        # Treat lines whose text length is > (page_width / 2 / ~6px char ≈ page_width/12) as crossing.
        if it["x0"] < mid and len(it["text"]) > page_width / 7:
            crossing += 1
    return crossing / max(len(lines), 1) < 0.25


def _global_body_size(doc: "fitz.Document") -> float:
    from collections import Counter
    counter: Counter[float] = Counter()
    for page in doc:
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        blocks = text_dict.get("blocks", []) if isinstance(text_dict, dict) else []  # type: ignore[attr-defined]
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != 0:
                continue
            for line in block.get("lines", []) or []:
                if not isinstance(line, dict):
                    continue
                for span in line.get("spans", []) or []:
                    if not isinstance(span, dict):
                        continue
                    t = span.get("text", "")
                    s = span.get("size", 0.0)
                    if t.strip():
                        counter[round(s, 1)] += len(t)
    return counter.most_common(1)[0][0] if counter else 10.0


def _render_page_snapshot(
    page: "fitz.Page", page_num: int, images_dir: Path
) -> Optional[str]:
    """Render the page to PNG at 2x DPI. Returns the filename or None on failure."""
    try:
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        name = f"page_{page_num + 1:03d}_full.png"
        pix.save(str(images_dir / name))
        return name
    except Exception as e:
        logger.warning(f"Failed to render page {page_num + 1} snapshot: {e}")
        return None


def _join_spans_with_spaces(spans: list[dict]) -> str:
    """Concatenate text spans inserting a space where bboxes show a gap.

    PyMuPDF emits each *visually positioned* run as its own span. In
    justified body text (e.g. the Transformer paper) each word becomes a
    separate span because kerning adjusts inter-word spacing; the span
    ``text`` carries no whitespace. Joining with ``""`` produces
    ``performingmodelsalsoconnect...``. Joining with ``" "`` instead would
    insert double spaces when spans *do* already contain whitespace.

    We use the span bbox to decide: insert a space when the left edge of
    the next span sits visibly to the right of the previous span's right
    edge (gap >= ~25% of average glyph width), and neither side already
    has surrounding whitespace.
    """
    parts: list[str] = []
    prev_right: float | None = None
    for span in spans:
        text = span.get("text", "")
        if not text.strip():
            # Whitespace-only span: still flush its content so explicit spacing
            # in the source survives, but skip the bbox bookkeeping.
            if text:
                parts.append(text)
            continue
        bbox = span.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        left, _, right, _ = bbox
        if prev_right is not None and parts:
            gap = left - prev_right
            avg_char = max((right - left) / max(len(text), 1), 1.0)
            prev_tail = parts[-1][-1:]
            needs_space = (
                gap >= avg_char * 0.25
                and not prev_tail.isspace()
                and not text[:1].isspace()
            )
            if needs_space:
                parts.append(" ")
        parts.append(text)
        prev_right = right
    return "".join(parts)


def _pymupdf_heading_level(
    font_size: float, body_size: float, text: str, is_bold: bool
) -> Optional[int]:
    if font_size <= body_size * 1.15:
        return None
    if len(text) > 120 and not is_bold:
        return None
    ratio = font_size / body_size
    if ratio >= 1.6:
        return 1
    if ratio >= 1.3:
        return 2
    if ratio >= 1.15:
        return 3
    return None


def _pymupdf_extract_images(
    page: "fitz.Page", page_num: int, images_dir: Path
) -> list[dict]:
    """Extract embedded raster images from a page.

    Returns ``[{'kind': 'image', 'y0': float, 'filename': str}, ...]`` so the
    caller can interleave image refs with text lines in reading order.
    """
    out: list[dict] = []
    for img_idx, img_info in enumerate(page.get_images(full=True)):
        xref = img_info[0]
        try:
            # Find where this image is positioned on the page so we can sort
            # by vertical reading order. get_image_rects may return multiple
            # rects when the image is reused — use the topmost one.
            rects = page.get_image_rects(xref) or []
            y0 = min((r.y0 for r in rects), default=0.0)

            pix = fitz.Pixmap(page.parent, xref)
            if pix.n > 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            name = f"page_{page_num + 1:03d}_img_{img_idx + 1:02d}.png"
            pix.save(str(images_dir / name))
            out.append({"kind": "image", "y0": y0, "filename": name})
        except Exception as e:
            logger.warning(f"Failed extracting image {img_idx + 1} on page {page_num + 1}: {e}")
    return out
