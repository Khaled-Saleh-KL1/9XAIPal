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
import sys
import time
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

    env = _build_mineru_env()

    # Memory safety for large books: a 656-page book extracted in one pass holds
    # the whole document's layout/OCR/MFR state in RAM at once and OOM-kills the
    # MinerU server inside Docker's memory-capped VM (the server child dies and
    # every subsequent CLI poll fails with "All connection attempts failed").
    # Bound peak RAM by extracting in page-range batches (MinerU's -s/-e flags,
    # 0-based inclusive) and merging the per-batch outputs back into one
    # document. Batching is skipped for small docs and can be disabled with
    # MINERU_PAGE_BATCH_SIZE=0.
    total_pages = get_page_count(pdf_path)
    batch_size = _int_env("MINERU_PAGE_BATCH_SIZE", 100)
    batches = _plan_page_batches(total_pages, batch_size)

    # MinerU 2.x/3.x's CLI is a thin client over a local FastAPI server. By
    # default it starts that server as a subprocess and tears it down when the
    # CLI exits. Inside the celery_worker container that lifecycle is fragile:
    # the server starts with a 1-concurrent-request semaphore (it detects the
    # host as Mac) and routinely crashes mid-extraction on long books, leaking
    # semaphores and leaving the port in a half-closed state. The client then
    # fails every poll with "All connection attempts failed".
    #
    # We work around this by starting the FastAPI server ourselves as a
    # long-lived background subprocess, pointing the CLI at it via `--api-url`,
    # and tearing it down ourselves after the CLI finishes. The single server is
    # reused across all page-batches (no per-batch restart) and we get a real
    # exit code to check after every batch.
    server_url: str | None = None
    server_proc: subprocess.Popen | None = None
    server_log_path: Path | None = None
    if not os.getenv("MINERU_API_URL"):
        server_url, server_proc, server_log_path = _start_mineru_api_server(env)
        logger.info(f"MinerU CLI will use external API at {server_url}")

    try:
        if len(batches) <= 1:
            # Single-shot path: small doc, unknown page count, or batching off.
            _run_mineru_cli(binary, pdf_path, output_dir, server_url, env, batches[0])
        else:
            # Batched path: extract each page range into its own temp dir using
            # the shared server, then merge into a single MinerU-style layout.
            logger.info(
                f"Large document ({total_pages} pages): extracting in "
                f"{len(batches)} page-batches of {batch_size}"
            )
            batch_root = output_dir.parent / f".{document_id}_batches"
            if batch_root.exists():
                shutil.rmtree(batch_root, ignore_errors=True)
            batch_root.mkdir(parents=True, exist_ok=True)
            try:
                batch_outputs: list[tuple[Path, int]] = []
                for i, (start, end) in enumerate(batches):
                    bdir = batch_root / f"batch_{i:04d}"
                    bdir.mkdir(parents=True, exist_ok=True)
                    logger.info(f"MinerU batch {i + 1}/{len(batches)}: pages {start}-{end}")
                    _run_mineru_cli(binary, pdf_path, bdir, server_url, env, (start, end))
                    batch_outputs.append((bdir, start))
                _merge_batch_outputs(batch_outputs, output_dir, pdf_path.stem)
            finally:
                shutil.rmtree(batch_root, ignore_errors=True)

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
    finally:
        if server_proc is not None:
            _stop_mineru_api_server(server_proc, server_log_path)


async def extract_pdf(pdf_path: Path, document_id: str) -> tuple[Path, str]:
    return extract_pdf_sync(pdf_path, document_id)


# ─────────────────────────────────────────────────────────────────────────────
# Extraction helpers: environment, page-batching, single CLI run, batch merge
# ─────────────────────────────────────────────────────────────────────────────


def _int_env(name: str, default: int) -> int:
    """Read an int env var, falling back to default on missing/garbage values."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _build_mineru_env() -> dict:
    """Build the environment MinerU runs under (offline + memory-safety toggles)."""
    # Local-first robustness: once MinerU's models are cached, the HuggingFace
    # hub still makes a network "is this up to date?" call on every run. A
    # transient network blip turns that into `All connection attempts failed`
    # and aborts the whole extraction. So we run MinerU fully offline whenever
    # the model cache already exists — cached weights are used directly and no
    # network is touched. Online only on a fresh install (cache absent) so
    # models can download the first time. Override with MINERU_FORCE_OFFLINE=1
    # (always offline) or MINERU_FORCE_OFFLINE=0 (always allow network).
    #
    # EXCEPTION: inside the celery_worker Docker image the models are pre-baked
    # at build time (Dockerfile.mineru runs `mineru-models-download`). There,
    # HF_HUB_OFFLINE=1 also blocks MinerU's post-processing stage (it reuses the
    # HF HTTP client to reach the optional LLM-aided VLM endpoint), surfacing as
    # the same "All connection attempts failed" error. The container signals
    # this with MINERU_BAKED_INTO_IMAGE=1; honour that by skipping the offline
    # toggle and letting HF hub's lightweight metadata check run normally.
    env = os.environ.copy()
    force_offline = os.getenv("MINERU_FORCE_OFFLINE")
    baked_into_image = os.getenv("MINERU_BAKED_INTO_IMAGE") == "1"
    hf_home = Path(os.getenv("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    if not baked_into_image and (force_offline == "1" or (force_offline != "0" and hf_home.exists())):
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        logger.info("MinerU running offline (model cache present); network disabled for extraction.")
    elif baked_into_image:
        logger.info("MinerU running with pre-baked models (Docker image); HF hub allowed to make metadata checks.")

    # Memory safety: MinerU processes pages in windows (default 64) and runs
    # formula recognition (MFR) on a whole window's equations at once. A smaller
    # window bounds peak RAM — slightly more model passes, far lower memory.
    # Override with MINERU_PROCESSING_WINDOW_SIZE in the environment.
    env.setdefault("MINERU_PROCESSING_WINDOW_SIZE", "16")
    return env


def _plan_page_batches(total_pages: Optional[int], batch_size: int) -> list:
    """Plan 0-based inclusive (start, end) page ranges for batched extraction.

    Returns ``[None]`` (a single whole-document pass) when the page count is
    unknown, batching is disabled (batch_size <= 0), or the document already
    fits in one batch. Otherwise returns consecutive ranges covering all pages.
    """
    if not total_pages or batch_size <= 0 or total_pages <= batch_size:
        return [None]
    batches = []
    start = 0
    while start < total_pages:
        end = min(start + batch_size - 1, total_pages - 1)
        batches.append((start, end))
        start = end + 1
    return batches


def _run_mineru_cli(
    binary: str,
    pdf_path: Path,
    out_dir: Path,
    api_url: Optional[str],
    env: dict,
    page_range: Optional[tuple],
) -> None:
    """Run one MinerU CLI invocation into ``out_dir``; raise MinerUError on failure.

    ``page_range`` is a 0-based inclusive ``(start, end)`` tuple, or ``None`` to
    extract the whole document.
    """
    cmd = [
        binary,
        "-p", str(pdf_path),
        "-o", str(out_dir),
        "-m", "auto",
        "-b", "pipeline",
        "-l", settings.mineru_lang,
    ]
    if page_range is not None:
        cmd += ["-s", str(page_range[0]), "-e", str(page_range[1])]
    if api_url:
        cmd += ["--api-url", api_url]
    logger.info(f"Running MinerU: {' '.join(cmd)}")

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


def _merge_batch_outputs(batch_outputs: list, output_dir: Path, stem: str) -> None:
    """Merge per-batch MinerU outputs into one MinerU-style layout under output_dir.

    Produces ``output_dir/<stem>/auto/`` containing a single concatenated
    ``<stem>_content_list.json``, ``<stem>.md``, and an ``images/`` dir — exactly
    the shape the downstream ``find_*`` helpers and chunker expect.

    - content_list: entries concatenated in page order; each batch reports
      batch-relative ``page_idx`` (verified: -s 5 -e 7 → page_idx 0,1,2), so we
      shift each by the batch's start page to restore absolute page numbers.
    - markdown: concatenated in page order.
    - images: copied into one ``images/`` dir. MinerU names images by content
      hash, so names are unique across batches and collisions (if any) are
      byte-identical files. Downstream links images by basename only, so the
      relative ``images/`` prefix in content_list/markdown stays valid.
    """
    doc_dir = output_dir / stem
    if doc_dir.exists():
        shutil.rmtree(doc_dir, ignore_errors=True)
    auto_dir = doc_dir / "auto"
    images_dir = auto_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    merged_content: list = []
    merged_md: list[str] = []

    for bdir, start in batch_outputs:
        cl = find_content_list(bdir)
        if cl is not None:
            entries = json.loads(cl.read_text(encoding="utf-8"))
            for e in entries:
                if isinstance(e.get("page_idx"), int):
                    e["page_idx"] += start
            merged_content.extend(entries)

        md = find_markdown_output(bdir)
        if md is not None:
            merged_md.append(md.read_text(encoding="utf-8"))

        for img in find_images(bdir):
            try:
                shutil.copy2(img, images_dir / img.name)
            except Exception as ex:
                logger.warning(f"Failed to copy batch image {img.name}: {ex}")

    (auto_dir / f"{stem}_content_list.json").write_text(
        json.dumps(merged_content, ensure_ascii=False), encoding="utf-8"
    )
    (auto_dir / f"{stem}.md").write_text("\n\n".join(merged_md), encoding="utf-8")
    logger.info(
        f"Merged {len(batch_outputs)} batches → {len(merged_content)} content blocks, "
        f"{sum(1 for _ in images_dir.iterdir())} images"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Long-lived MinerU FastAPI server
# ─────────────────────────────────────────────────────────────────────────────
#
# MinerU 2.x/3.x's CLI is a thin client that talks to a local FastAPI server
# (mineru.cli.fast_api). The CLI normally starts that server as a short-lived
# subprocess, runs the extraction, and kills the server. Inside the
# celery_worker container that lifecycle breaks on long books (500+ pages):
# the server crashes mid-extraction, leaks semaphores, and the client polls
# fail with "All connection attempts failed".
#
# The fix is to start the server ourselves as a long-lived subprocess, point
# the CLI at it via --api-url, and tear it down after the CLI exits. The
# server stays up for the full extraction and we get a real exit code.

def _start_mineru_api_server(env: dict) -> tuple[str, "subprocess.Popen", Path]:
    """Start the MinerU FastAPI server as a background subprocess.

    Returns ``(base_url, process, log_path)``. The caller MUST call
    ``_stop_mineru_api_server`` in a ``finally`` block.
    """
    import socket
    # Pick a free port deterministically (avoids races with other Celery
    # workers trying to start their own server).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    host = "127.0.0.1"
    base_url = f"http://{host}:{port}"

    log_dir = Path(env.get("STORAGE_ROOT", "/data/storage")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mineru-api.log"

    cmd = [
        sys.executable,
        "-m",
        "mineru.cli.fast_api",
        "--host", host,
        "--port", str(port),
    ]
    logger.info(f"Starting MinerU API server: {' '.join(cmd)} (logs → {log_path})")
    log_file = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        # New process group so we can kill the whole tree on shutdown.
        start_new_session=True,
    )

    # Wait for the server to be ready (poll the health endpoint).
    import httpx
    deadline = time.monotonic() + 300.0  # 5 min startup budget
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_file.close()
            raise MinerUError(
                f"MinerU API server exited prematurely (rc={proc.returncode}). "
                f"Check logs: {log_path}"
            )
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                logger.info(f"MinerU API server ready at {base_url} (pid={proc.pid})")
                return base_url, proc, log_path
        except Exception:
            pass
        time.sleep(1.0)

    # Timed out waiting for the server to come up.
    _stop_mineru_api_server(proc, log_path)
    raise MinerUError(
        f"MinerU API server did not become ready within 300s. Check logs: {log_path}"
    )


def _stop_mineru_api_server(proc: "subprocess.Popen", log_path: Path | None) -> None:
    """Gracefully stop the MinerU API server subprocess."""
    if proc.poll() is not None:
        return  # already exited
    logger.info(f"Stopping MinerU API server (pid={proc.pid})...")
    try:
        # Try a graceful SIGTERM first (gives the server a chance to flush).
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            logger.warning("MinerU API server did not exit on SIGTERM; sending SIGKILL")
            proc.kill()
            proc.wait(timeout=5)
    except Exception as e:
        logger.warning(f"Error stopping MinerU API server: {e}")


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
