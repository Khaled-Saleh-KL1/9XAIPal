"""VLM PDF extraction (Qwen3-VL via Ollama Cloud) -> MinerU-compatible content_list."""
from __future__ import annotations
import base64, json
from pathlib import Path
import fitz  # PyMuPDF
import httpx
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

def render_pages(pdf_path: Path, dpi: int) -> list[bytes]:
    doc = fitz.open(pdf_path)
    try:
        return [doc[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(doc.page_count)]
    finally:
        doc.close()
