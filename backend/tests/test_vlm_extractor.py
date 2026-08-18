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


def _resp(content: str):
    """Mock httpx client whose /api/chat returns ``content`` as the message."""
    client = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"message": {"content": content}}
    resp.raise_for_status.return_value = None
    client.post.return_value = resp
    return client


def test_call_vlm_page_strips_markdown_code_fence():
    """gemma4:31b wraps its reply in ```json ... ``` even with format="json".

    Observed against Ollama Cloud on 2026-08-17: json.loads() on the raw
    content raised "Expecting value: line 1 column 1 (char 0)", so every page
    silently fell back to PyMuPDF text-only extraction.
    """
    fenced = '```json\n{"blocks": [{"type": "text", "text": "Body."}]}\n```'
    blocks = vlm_client.call_vlm_page(b"\x89PNG", "gemma4:31b", _resp(fenced))
    assert blocks == [{"type": "text", "text": "Body."}]


def test_call_vlm_page_strips_bare_fence_without_language():
    bare = '```\n{"blocks": [{"type": "text", "text": "Body."}]}\n```'
    blocks = vlm_client.call_vlm_page(b"\x89PNG", "gemma4:31b", _resp(bare))
    assert blocks[0]["text"] == "Body."


def test_call_vlm_page_normalizes_heading_aliases():
    """The model emits "title"/"header" instead of the prompted text_level.

    The chunker keys headings off type="text" + text_level, so unmapped
    aliases would land as plain body text and break chapter navigation.
    """
    content = json.dumps({"blocks": [
        {"type": "title", "text": "Attention Is All You Need"},
        {"type": "heading", "text": "1  Introduction"},
        {"type": "paragraph", "text": "Body."},
        {"type": "figure", "bbox": [0, 0, 10, 10]},
        {"type": "formula", "text": "$$x$$"},
    ]})
    blocks = vlm_client.call_vlm_page(b"\x89PNG", "gemma4:31b", _resp(content))
    assert blocks[0]["type"] == "text" and blocks[0]["text_level"] == 1
    assert blocks[1]["type"] == "text" and blocks[1]["text_level"] == 2
    assert blocks[2]["type"] == "text" and "text_level" not in blocks[2]
    assert blocks[3]["type"] == "image"
    assert blocks[4]["type"] == "equation"


def test_call_vlm_page_leaves_page_furniture_for_the_chunker_to_drop():
    """"header"/"footer"/"page_number" are the chunker's _DROP_TYPES.

    gemma4 uses "header" for running page furniture (the publisher notice on
    page 1), so it must pass through untouched — mapping it to a heading would
    resurrect that furniture as document structure.
    """
    content = json.dumps({"blocks": [
        {"type": "header", "text": "Provided proper attribution..."},
        {"type": "page_number", "text": "2"},
    ]})
    blocks = vlm_client.call_vlm_page(b"\x89PNG", "gemma4:31b", _resp(content))
    assert [b["type"] for b in blocks] == ["header", "page_number"]


def test_call_vlm_page_survives_unparseable_content():
    """A non-JSON reply must yield no blocks rather than raise, so one bad
    page degrades to the PyMuPDF fallback instead of failing the document."""
    assert vlm_client.call_vlm_page(b"\x89PNG", "gemma4:31b", _resp("sorry, I cannot")) == []


def test_crop_prefers_the_real_embedded_image_over_the_models_bbox(tmp_path):
    """A VLM guesses bounding boxes; the PDF states them exactly.

    Observed on "Attention Is All You Need": the model's bbox for Figure 1 was
    far enough off that the saved crop was mostly whitespace with the diagram
    sliced off at the edge. PyMuPDF reports the embedded image's true rect, so
    that must win whenever the page actually has one.
    """
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # A real embedded raster, placed well away from the bogus bbox below.
    img = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 120), 0)
    img.set_rect(img.irect, (255, 0, 0))
    page.insert_image(fitz.Rect(200, 100, 280, 220), pixmap=img)
    p = tmp_path / "fig.pdf"
    doc.save(p); doc.close()

    d2 = fitz.open(p)
    dest = tmp_path / "out.png"
    # Deliberately wrong bbox — the kind of thing the model actually returns.
    assert vlm_client._crop_figure(d2, 0, [10, 10, 60, 700], dpi=150, dest=dest)
    out = fitz.Pixmap(dest)
    d2.close()

    scale = 150 / 72.0
    exp_w = int(80 * scale)   # the image is 80pt wide
    exp_h = int(120 * scale)
    assert abs(out.width - exp_w) <= 4, f"cropped {out.width}px wide, expected ~{exp_w}"
    assert abs(out.height - exp_h) <= 4, f"cropped {out.height}px tall, expected ~{exp_h}"


def test_crop_falls_back_to_bbox_when_page_has_no_embedded_image(tmp_path):
    """Vector-only figures have no image rect, so the model's bbox is all we have."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(fitz.Rect(100, 100, 200, 200), color=(0, 0, 0))
    p = tmp_path / "vec.pdf"
    doc.save(p); doc.close()

    d2 = fitz.open(p)
    dest = tmp_path / "out2.png"
    assert vlm_client._crop_figure(d2, 0, [0, 0, 300, 300], dpi=72, dest=dest)
    out = fitz.Pixmap(dest)
    d2.close()
    assert abs(out.width - 300) <= 4 and abs(out.height - 300) <= 4


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
