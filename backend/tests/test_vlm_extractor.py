import fitz
from pathlib import Path
from app.core.config import settings
from app.extraction import vlm_client


def test_extractor_settings_defaults():
    assert settings.extractor_provider == "mineru"           # off by default
    assert settings.extractor_vlm_model.startswith("qwen3-vl")
    assert settings.extractor_vlm_dpi >= 72
    assert settings.extractor_vlm_max_pages == 0             # 0 = unlimited
    assert settings.extractor_vlm_concurrency >= 1


def _make_pdf(tmp_path) -> Path:
    doc = fitz.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i+1} heading")
    p = tmp_path / "doc.pdf"
    doc.save(p); doc.close()
    return p


def test_render_pages_returns_one_png_per_page(tmp_path):
    pngs = vlm_client.render_pages(_make_pdf(tmp_path), dpi=100)
    assert len(pngs) == 2
    assert pngs[0][:8] == b"\x89PNG\r\n\x1a\n"   # PNG magic
