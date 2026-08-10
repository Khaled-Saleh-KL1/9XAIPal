import json
from unittest.mock import MagicMock, patch
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


def test_call_vlm_page_parses_blocks():
    fake = {"message": {"content": json.dumps({"blocks": [
        {"type": "text", "text": "Intro", "text_level": 1},
        {"type": "text", "text": "Body text."},
    ]})}}
    client = MagicMock()
    resp = MagicMock(); resp.json.return_value = fake; resp.raise_for_status.return_value = None
    client.post.return_value = resp
    blocks = vlm_client.call_vlm_page(b"\x89PNG", "qwen3-vl:test", client)
    assert blocks[0]["text_level"] == 1
    assert blocks[1]["text"] == "Body text."
    # sends base64 image + format=json
    _, kwargs = client.post.call_args
    assert kwargs["json"]["format"] == "json"
    assert kwargs["json"]["messages"][0]["images"]


def test_extract_via_vlm_writes_content_list_and_crops(tmp_path):
    pdf = _make_pdf(tmp_path)                     # 2-page PDF from Task 2 helper
    out = tmp_path / "out"
    def fake_call(png, model, client):
        return [
            {"type": "text", "text": "Title", "text_level": 1},
            {"type": "image", "bbox": [10, 10, 60, 60], "img_caption": ["Fig 1"]},
        ]
    with patch.object(vlm_client, "call_vlm_page", side_effect=fake_call):
        result = vlm_client.extract_via_vlm(pdf, out)
    data = json.loads((result / "content_list.json").read_text())
    assert result == out
    assert any(b["type"] == "text" and b.get("text_level") == 1 for b in data)
    imgs = [b for b in data if b["type"] == "image"]
    assert imgs and imgs[0]["img_path"].startswith("images/")
    assert "bbox" not in imgs[0]
    assert (out / imgs[0]["img_path"]).exists()          # crop was saved
    assert all("page_idx" in b for b in data)


def test_extract_via_vlm_missing_bbox_falls_back_to_whole_page(tmp_path):
    pdf = _make_pdf(tmp_path)
    out = tmp_path / "out"
    def fake_call(png, model, client):
        return [
            {"type": "image", "img_caption": ["Fig 1"]},   # no bbox key at all
        ]
    with patch.object(vlm_client, "call_vlm_page", side_effect=fake_call):
        result = vlm_client.extract_via_vlm(pdf, out)
    data = json.loads((result / "content_list.json").read_text())
    imgs = [b for b in data if b["type"] == "image"]
    assert imgs and imgs[0]["img_path"].startswith("images/")
    assert "bbox" not in imgs[0]
    assert (out / imgs[0]["img_path"]).exists()          # whole-page crop was saved


def test_resolve_extractor_uses_vlm_when_selected(tmp_path, monkeypatch):
    from app.extraction import pipeline_sync
    monkeypatch.setattr(pipeline_sync.settings, "extractor_provider", "vlm")
    called = {}

    def fake_extract_via_vlm(p, o):
        called["vlm"] = (p, o)
        return o

    monkeypatch.setattr(pipeline_sync, "extract_via_vlm", fake_extract_via_vlm)
    out_dir, extractor = pipeline_sync.resolve_extractor(tmp_path / "x.pdf", tmp_path / "o")
    assert "vlm" in called
    assert out_dir == tmp_path / "o"
    assert extractor == "vlm"


def test_resolve_extractor_uses_mineru_by_default(tmp_path, monkeypatch):
    """provider unset/'mineru' must still route through the existing MinerU
    client path (extract_pdf_sync), unchanged from before this dispatcher
    existed."""
    from app.extraction import pipeline_sync
    monkeypatch.setattr(pipeline_sync.settings, "extractor_provider", "mineru")
    called = {}
    fake_out = tmp_path / "o"

    def fake_extract_pdf_sync(pdf_path, document_id):
        called["mineru"] = (pdf_path, document_id)
        return fake_out, "mineru"

    monkeypatch.setattr(pipeline_sync, "extract_pdf_sync", fake_extract_pdf_sync)
    out_dir, extractor = pipeline_sync.resolve_extractor(tmp_path / "x.pdf", fake_out)
    assert "mineru" in called
    assert called["mineru"] == (tmp_path / "x.pdf", "o")   # document_id derived from output_dir.name
    assert out_dir == fake_out
    assert extractor == "mineru"
