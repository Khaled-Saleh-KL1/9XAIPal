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
    "Drop running headers, footers, and page numbers. Output JSON only."
)

def render_pages(pdf_path: Path, dpi: int) -> list[bytes]:
    doc = fitz.open(pdf_path)
    try:
        return [doc[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(doc.page_count)]
    finally:
        doc.close()

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
    data = json.loads(content)
    blocks = data.get("blocks", data) if isinstance(data, dict) else data
    return [b for b in blocks if isinstance(b, dict) and b.get("type")]

def _crop_figure(doc, page_idx: int, bbox, dpi: int, dest: Path) -> bool:
    try:
        page = doc[page_idx]
        pix = page.get_pixmap(dpi=dpi)
        crop = pix                          # default: whole page (missing/bad bbox)
        try:
            x0, y0, x1, y1 = (int(v) for v in bbox)
            irect = fitz.IRect(x0, y0, x1, y1) & fitz.IRect(0, 0, pix.width, pix.height)
            if not irect.is_empty and irect.width >= 4 and irect.height >= 4:
                target = fitz.Pixmap(pix.colorspace, irect, pix.alpha)
                target.copy(pix, irect)
                crop = target
        except (ValueError, TypeError):
            pass                             # missing/malformed bbox -> whole page
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
                except Exception as e:                     # per-page fallback
                    logger.warning(f"VLM page {pidx} failed ({e}); PyMuPDF text fallback")
                    text = doc[pidx].get_text().strip()
                    blocks = [{"type": "text", "text": text}] if text else []
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
