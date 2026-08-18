"""VLM PDF extraction (Qwen3-VL via Ollama Cloud) -> MinerU-compatible content_list."""
from __future__ import annotations
import base64, json
from pathlib import Path
import fitz  # PyMuPDF
import httpx
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.ollama_client import _ollama_headers

logger = get_logger(__name__)

PAGE_PROMPT = (
    "You are a document-structure extractor. The image is ONE page of a PDF. "
    "Return ONLY a JSON object {\"blocks\": [...]} listing blocks in reading order. "
    "Each block is one of:\n"
    '{"type":"text","text":"...","text_level":1}  (text_level 1-3 ONLY for headings; omit for body)\n'
    '{"type":"equation","text":"$$ latex $$"}\n'
    '{"type":"table","table_body":"<table>...</table>","table_caption":["..."]}\n'
    '{"type":"image","bbox":[x0,y0,x1,y1],"img_caption":["..."]}  (bbox in PIXELS of this image)\n'
    "Drop running headers, footers, and page numbers.\n"
    # Both rules below come from observed failures on real papers, not theory.
    # Ablation tables leave most cells blank (a blank means "same as the base
    # row"), and the model was collapsing those blanks — shifting values into
    # the neighbouring column, e.g. reporting d_k figures under h.
    "TABLES: emit every column for every row, in order. A visually blank cell "
    "MUST be an empty <td></td> — never skip it and never shift a value into a "
    "neighbouring column. Blank cells are meaningful: they mean 'unchanged from "
    "the row above'. Count the header columns and give every row that many cells.\n"
    # Long paragraphs were being cut mid-sentence, silently losing text.
    "TEXT: transcribe each paragraph in full, to its final punctuation. Never "
    "truncate, summarise, or end a block mid-sentence.\n"
    "Output JSON only."
)

def render_pages(pdf_path: Path, dpi: int) -> list[bytes]:
    doc = fitz.open(pdf_path)
    try:
        return [doc[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(doc.page_count)]
    finally:
        doc.close()

def _strip_code_fence(content: str) -> str:
    """Unwrap a ```json ... ``` fence if the model added one.

    gemma4:31b wraps its reply in a markdown fence even when the request sets
    ``format="json"``. Feeding that straight to json.loads() raises
    "Expecting value: line 1 column 1 (char 0)", which made every page fall
    back to PyMuPDF text-only extraction.
    """
    s = content.strip()
    if not s.startswith("```"):
        return s
    s = s.split("\n", 1)[1] if "\n" in s else ""      # drop the ```json line
    end = s.rfind("```")
    return (s[:end] if end != -1 else s).strip()


# Types models emit instead of the prompted schema. The chunker keys headings
# off type="text" + text_level, so these have to be mapped or headings arrive
# as plain body text and chapter navigation loses them.
#
# Deliberately NOT mapped: "header", "footer" and "page_number". Those are the
# chunker's _DROP_TYPES for running page furniture, and gemma4 uses "header"
# in exactly that sense (e.g. the publisher notice atop page 1). Passing them
# through unchanged lets the chunker drop them; mapping "header" to a heading
# would resurrect page furniture as document structure.
_HEADING_ALIASES = {"title": 1, "heading": 2, "section": 2,
                    "section_header": 2, "subtitle": 2}
_TYPE_ALIASES = {"figure": "image", "picture": "image", "formula": "equation",
                 "math": "equation", "paragraph": "text", "body": "text",
                 "plain_text": "text", "caption": "text"}


def _normalize_block(b: dict) -> dict:
    etype = str(b.get("type", "")).strip().lower()
    if etype in _HEADING_ALIASES:
        b = {**b, "type": "text", "text_level": b.get("text_level") or _HEADING_ALIASES[etype]}
    elif etype in _TYPE_ALIASES:
        b = {**b, "type": _TYPE_ALIASES[etype]}
    return b


def call_vlm_page(png: bytes, model: str, client: httpx.Client) -> list[dict]:
    b64 = base64.b64encode(png).decode()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PAGE_PROMPT, "images": [b64]}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    resp = client.post(f"{settings.ollama_base_url}/api/chat",
                       json=payload, headers=_ollama_headers())
    resp.raise_for_status()
    content = resp.json().get("message", {}).get("content", "") or "{}"
    try:
        data = json.loads(_strip_code_fence(content) or "{}")
    except json.JSONDecodeError:
        # One unusable page must not fail the document — the caller falls back
        # to PyMuPDF text for this page.
        logger.warning("VLM returned unparseable content (%d chars); skipping page",
                       len(content))
        return []
    blocks = data.get("blocks", data) if isinstance(data, dict) else data
    if not isinstance(blocks, list):
        return []
    return [_normalize_block(b) for b in blocks if isinstance(b, dict) and b.get("type")]

def _embedded_image_rects(page, dpi: int) -> list:
    """Pixel rects of raster images embedded in the page, at `dpi`.

    The PDF states these exactly, whereas a VLM only guesses bounding boxes —
    and guesses them badly enough to crop a figure down to whitespace.
    """
    scale = dpi / 72.0                        # PDF points -> pixels
    rects = []
    try:
        for img in page.get_images(full=True):
            for r in page.get_image_rects(img[0]):
                ir = fitz.IRect(int(r.x0 * scale), int(r.y0 * scale),
                                int(r.x1 * scale), int(r.y1 * scale))
                if ir.width >= 4 and ir.height >= 4:
                    rects.append(ir)
    except Exception:                          # pragma: no cover - defensive
        pass
    return rects


def _area(r) -> int:
    return max(0, r.width) * max(0, r.height)


def _choose_crop_rect(page, bbox, dpi: int, pix):
    """Where to actually cut the figure from. None means 'use the whole page'.

    Order of trust: the PDF's own image geometry, then the model's bbox, then
    the whole page.
    """
    page_rect = fitz.IRect(0, 0, pix.width, pix.height)

    guess = None
    try:
        x0, y0, x1, y1 = (int(v) for v in bbox)
        cand = fitz.IRect(x0, y0, x1, y1) & page_rect
        if not cand.is_empty and cand.width >= 4 and cand.height >= 4:
            guess = cand
    except (ValueError, TypeError):
        guess = None                           # missing/malformed bbox

    embedded = _embedded_image_rects(page, dpi)
    if embedded:
        # With several images on a page, the model's guess still tells us WHICH
        # one it meant, even though its coordinates are unreliable.
        if guess is not None:
            overlapping = [r for r in embedded if not (r & guess).is_empty]
            if overlapping:
                return max(overlapping, key=lambda r: _area(r & guess))
        return max(embedded, key=_area)

    return guess                               # vector-only figure, or nothing


def _crop_figure(doc, page_idx: int, bbox, dpi: int, dest: Path) -> bool:
    try:
        page = doc[page_idx]
        pix = page.get_pixmap(dpi=dpi)
        crop = pix                             # default: whole page
        irect = _choose_crop_rect(page, bbox, dpi, pix)
        if irect is not None:
            target = fitz.Pixmap(pix.colorspace, irect, pix.alpha)
            target.copy(pix, irect)
            crop = target
        crop.save(dest)
        return True
    except Exception as e:
        logger.warning(f"figure crop failed p{page_idx}: {e}")
        return False

def extract_via_vlm(pdf_path: Path, output_dir: Path) -> Path:
    dpi = settings.extractor_vlm_dpi
    model = settings.extractor_vlm_model
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    pngs = render_pages(pdf_path, dpi)
    if settings.extractor_vlm_max_pages:
        pngs = pngs[: settings.extractor_vlm_max_pages]
    doc = fitz.open(pdf_path)
    content: list[dict] = []
    fig_n = 0
    try:
        with httpx.Client(timeout=180.0) as client:
            for pidx, png in enumerate(pngs):
                try:
                    blocks = call_vlm_page(png, model, client)
                except Exception as e:                     # transport/HTTP failure
                    logger.warning(f"VLM page {pidx} failed ({e}); PyMuPDF text fallback")
                    blocks = []
                # An EMPTY result needs the same fallback as a raised one.
                # call_vlm_page returns [] for unparseable JSON rather than
                # raising (so one bad page cannot fail the document), which
                # meant the `except` above never fired for that case and the
                # page silently contributed nothing — it cost the live corpus
                # five pages, including this paper's entire Introduction.
                # Keying off the empty list covers both paths.
                if not blocks:
                    text = doc[pidx].get_text().strip()
                    if text:
                        logger.warning(
                            f"VLM page {pidx} produced no blocks; "
                            f"PyMuPDF text fallback ({len(text)} chars)"
                        )
                        blocks = [{"type": "text", "text": text}]
                    else:
                        # Genuinely blank page — nothing to recover, and
                        # emitting an empty block would be worse than nothing.
                        logger.info(f"VLM page {pidx} produced no blocks and the page has no text")
                for b in blocks:
                    b["page_idx"] = pidx
                    if b.get("type") == "image":
                        fig_n += 1
                        name = f"images/fig_{pidx+1}_{fig_n}.png"
                        if _crop_figure(doc, pidx, b.get("bbox") or [], dpi, output_dir / name):
                            b["img_path"] = name
                        b.pop("bbox", None)
                    content.append(b)
    finally:
        doc.close()
    (output_dir / "content_list.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8")
    logger.info(f"VLM extraction: {len(content)} blocks over {len(pngs)} pages")
    return output_dir
