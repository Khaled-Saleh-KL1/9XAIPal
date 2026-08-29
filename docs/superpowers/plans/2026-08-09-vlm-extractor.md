# Cloud VLM Extractor (Qwen3‑VL) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Qwen3‑VL (Ollama Cloud) PDF extractor that emits a MinerU‑compatible `content_list.json`, selectable via `EXTRACTOR_PROVIDER=vlm`, so the backend can run without MinerU/torch.

**Architecture:** New `app/extraction/vlm_client.py` renders each PDF page to PNG with PyMuPDF, asks Qwen3‑VL (via the existing Ollama Cloud `/api/chat` + Bearer auth) for a JSON array of structural blocks, assembles them into `content_list.json` (+ cropped figure assets), and `run_pipeline_sync` dispatches to it based on a new `EXTRACTOR_PROVIDER` setting. Everything downstream (`create_chunks_from_content_list`, embeddings, reading modes) is unchanged.

**Tech Stack:** Python 3.11, PyMuPDF (`fitz`), httpx, pytest/pytest-asyncio. Model: Qwen3‑VL on Ollama Cloud.

## Global Constraints

- New code path must be **off by default**: `EXTRACTOR_PROVIDER` defaults to `mineru`. Backend CI (`python -m pytest`) must stay green with no network.
- Reuse existing Ollama auth: `app.llm.ollama_client._ollama_headers()` (Bearer `OLLAMA_API_KEY`) against `settings.ollama_base_url` (`https://ollama.com`).
- No new dependencies (PyMuPDF + httpx already in `requirements.txt`).
- Output must match the `content_list.json` block schema consumed by `create_chunks_from_content_list` (see spec `docs/superpowers/specs/2026-08-09-vlm-extraction-design.md`): blocks `text`(+`text_level`), `equation`(`$$…$$`), `image`(`img_path`,`img_caption`), `table`(`table_body`,`table_caption`); every block has `page_idx` (0‑indexed).
- Never fail a whole document on one bad page/block: validate & drop bad blocks; fall back to PyMuPDF text for a failed page.

---

## File Structure

- Create `backend/app/extraction/vlm_client.py`: rendering, VLM call, parse, assembly, cropping.
- Modify `backend/app/core/config.py`: add extractor settings.
- Modify `backend/app/extraction/pipeline_sync.py`: dispatch on `EXTRACTOR_PROVIDER`.
- Create `backend/tests/test_vlm_extractor.py`: unit tests (mocked VLM + fitz on a tiny generated PDF).

---

### Task 1: Extractor settings

**Files:**
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_vlm_extractor.py`

**Interfaces:**
- Produces: `settings.extractor_provider: str`, `settings.extractor_vlm_model: str`, `settings.extractor_vlm_dpi: int`, `settings.extractor_vlm_max_pages: int`, `settings.extractor_vlm_concurrency: int`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_vlm_extractor.py
from app.core.config import settings

def test_extractor_settings_defaults():
    assert settings.extractor_provider == "mineru"           # off by default
    assert settings.extractor_vlm_model.startswith("qwen3-vl")
    assert settings.extractor_vlm_dpi >= 72
    assert settings.extractor_vlm_max_pages == 0             # 0 = unlimited
    assert settings.extractor_vlm_concurrency >= 1
```
- [ ] **Step 2: Run test to verify it fails**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_extractor_settings_defaults -v`
Expected: FAIL (`AttributeError: extractor_provider`)
- [ ] **Step 3: Add settings to `config.py`** (near the MinerU block)
```python
    # ── PDF extractor selection ─────────────────────────────────────────────
    # "mineru" (default, local high-quality), "vlm" (cloud Qwen3-VL via Ollama
    # Cloud — no torch/MinerU needed), or "pymupdf" (plain text fallback).
    extractor_provider: str = "mineru"
    extractor_vlm_model: str = "qwen3-vl:235b-cloud"  # confirm exact cloud tag; configurable
    extractor_vlm_dpi: int = 180
    extractor_vlm_max_pages: int = 0        # 0 = unlimited; guardrail for credit use
    extractor_vlm_concurrency: int = 3      # bounded to respect Ollama Cloud rate limits
```
- [ ] **Step 4: Run test to verify it passes**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_extractor_settings_defaults -v`
Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add backend/app/core/config.py backend/tests/test_vlm_extractor.py
git commit -m "feat(extraction): add EXTRACTOR_PROVIDER + VLM settings"
```

---

### Task 2: Render PDF pages to PNG

**Files:**
- Create: `backend/app/extraction/vlm_client.py`
- Test: `backend/tests/test_vlm_extractor.py`

**Interfaces:**
- Produces: `render_pages(pdf_path: Path, dpi: int) -> list[bytes]` (one PNG per page).

- [ ] **Step 1: Write the failing test** (build a 2‑page PDF with fitz, render it)
```python
import fitz
from pathlib import Path
from app.extraction import vlm_client

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
```
- [ ] **Step 2: Run test to verify it fails**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_render_pages_returns_one_png_per_page -v`
Expected: FAIL (`ModuleNotFoundError`/`AttributeError`)
- [ ] **Step 3: Implement `render_pages`**
```python
# backend/app/extraction/vlm_client.py
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

def render_pages(pdf_path: Path, dpi: int) -> list[bytes]:
    doc = fitz.open(pdf_path)
    try:
        return [doc[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(doc.page_count)]
    finally:
        doc.close()
```
- [ ] **Step 4: Run test to verify it passes**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_render_pages_returns_one_png_per_page -v`
Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add backend/app/extraction/vlm_client.py backend/tests/test_vlm_extractor.py
git commit -m "feat(extraction): render PDF pages to PNG for VLM"
```

---

### Task 3: Call Qwen3‑VL for one page and parse blocks

**Files:**
- Modify: `backend/app/extraction/vlm_client.py`
- Test: `backend/tests/test_vlm_extractor.py`

**Interfaces:**
- Consumes: `render_pages`.
- Produces: `PAGE_PROMPT: str`; `call_vlm_page(png: bytes, model: str, client: httpx.Client) -> list[dict]` (raw blocks, no `page_idx` yet).

- [ ] **Step 1: Write the failing test** (mock httpx so no network)
```python
from unittest.mock import patch, MagicMock

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
```
- [ ] **Step 2: Run test to verify it fails**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_call_vlm_page_parses_blocks -v`
Expected: FAIL (`AttributeError: call_vlm_page`)
- [ ] **Step 3: Implement prompt + `call_vlm_page`**
```python
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
```
- [ ] **Step 4: Run test to verify it passes**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_call_vlm_page_parses_blocks -v`
Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add backend/app/extraction/vlm_client.py backend/tests/test_vlm_extractor.py
git commit -m "feat(extraction): Qwen3-VL per-page call + block parse"
```

---

### Task 4: Assemble content_list + crop figure assets + write output

**Files:**
- Modify: `backend/app/extraction/vlm_client.py`
- Test: `backend/tests/test_vlm_extractor.py`

**Interfaces:**
- Consumes: `render_pages`, `call_vlm_page`.
- Produces: `extract_via_vlm(pdf_path: Path, output_dir: Path) -> Path` (returns `output_dir` containing `content_list.json` and `images/`). Adds `page_idx` (0‑indexed) to every block; converts `image` bbox → cropped PNG under `images/` with `img_path` set; drops `bbox` from the emitted block.

- [ ] **Step 1: Write the failing test** (mock `call_vlm_page` so no network; real fitz render + crop)
```python
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
```
- [ ] **Step 2: Run test to verify it fails**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_extract_via_vlm_writes_content_list_and_crops -v`
Expected: FAIL (`AttributeError: extract_via_vlm`)
- [ ] **Step 3: Implement `extract_via_vlm`**
```python
def _crop_figure(doc, page_idx: int, bbox, dpi: int, dest: Path) -> bool:
    try:
        page = doc[page_idx]
        pix = page.get_pixmap(dpi=dpi)
        x0, y0, x1, y1 = (int(v) for v in bbox)
        irect = fitz.IRect(x0, y0, x1, y1) & fitz.IRect(0, 0, pix.width, pix.height)
        if irect.is_empty or irect.width < 4 or irect.height < 4:
            crop = pix                      # bad bbox -> whole page
        else:
            crop = fitz.Pixmap(pix, irect)
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
```
- [ ] **Step 4: Run test to verify it passes**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_extract_via_vlm_writes_content_list_and_crops -v`
Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add backend/app/extraction/vlm_client.py backend/tests/test_vlm_extractor.py
git commit -m "feat(extraction): assemble content_list + crop figures (VLM)"
```

---

### Task 5: Dispatch in the pipeline on EXTRACTOR_PROVIDER

**Files:**
- Modify: `backend/app/extraction/pipeline_sync.py` (the MinerU call site around lines 257–279)
- Test: `backend/tests/test_vlm_extractor.py`

**Interfaces:**
- Consumes: `extract_via_vlm`, existing MinerU `extract_pdf_sync`.
- Produces: `resolve_extractor(pdf_path: Path, output_dir: Path) -> Path` used by `run_pipeline_sync`; dispatches by `settings.extractor_provider`.

- [ ] **Step 1: Write the failing test**
```python
def test_resolve_extractor_uses_vlm_when_selected(tmp_path, monkeypatch):
    from app.extraction import pipeline_sync
    monkeypatch.setattr("app.core.config.settings.extractor_provider", "vlm")
    called = {}
    monkeypatch.setattr(pipeline_sync, "extract_via_vlm",
                        lambda p, o: called.setdefault("vlm", (p, o)) or o)
    out = pipeline_sync.resolve_extractor(tmp_path / "x.pdf", tmp_path / "o")
    assert "vlm" in called and out == tmp_path / "o"
```
- [ ] **Step 2: Run test to verify it fails**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py::test_resolve_extractor_uses_vlm_when_selected -v`
Expected: FAIL (`AttributeError: resolve_extractor`)
- [ ] **Step 3: Implement dispatcher in `pipeline_sync.py`**
Add import `from app.extraction.vlm_client import extract_via_vlm` and:
```python
def resolve_extractor(pdf_path: Path, output_dir: Path) -> Path:
    """Route extraction by EXTRACTOR_PROVIDER; returns the output dir with content_list.json."""
    provider = (settings.extractor_provider or "mineru").lower()
    if provider == "vlm":
        return extract_via_vlm(pdf_path, output_dir)
    # "mineru"/"pymupdf" keep the existing MinerU-client path (it already
    # honors ALLOW_PYMUPDF_FALLBACK). Replace the direct MinerU call in
    # run_pipeline_sync's Step 1 with a call to this function.
    return extract_pdf_sync(pdf_path, output_dir)  # existing behavior
```
Then in `run_pipeline_sync` Step 1, replace the direct `extract_pdf_sync(...)` call with `resolve_extractor(...)`, keeping the surrounding `output_dir`/`content_list` handling unchanged.
- [ ] **Step 4: Run tests (new + regression) to verify pass**
Run: `cd backend && python -m pytest tests/test_vlm_extractor.py tests/test_ingestion_pipeline.py -v`
Expected: PASS (VLM dispatch works; MinerU path unchanged when provider≠vlm)
- [ ] **Step 5: Commit**
```bash
git add backend/app/extraction/pipeline_sync.py backend/tests/test_vlm_extractor.py
git commit -m "feat(extraction): dispatch extractor on EXTRACTOR_PROVIDER"
```

---

### Task 6: Local integration verification (manual, real key, not CI)

**Files:** none (verification only). Document results in the PR description.

- [ ] **Step 1:** In `backend/.env` set `EXTRACTOR_PROVIDER=vlm` and confirm `OLLAMA_API_KEY` + `OLLAMA_BASE_URL=https://ollama.com` are set. Confirm the exact Qwen3‑VL cloud tag exists: `curl -s https://ollama.com/api/tags -H "Authorization: Bearer $OLLAMA_API_KEY"` (or the model page) and adjust `EXTRACTOR_VLM_MODEL` if needed.
- [ ] **Step 2:** Run a one-off extraction against a sample:
```bash
cd backend && python -c "from pathlib import Path; from app.extraction.vlm_client import extract_via_vlm; \
import tempfile, json; d=Path(tempfile.mkdtemp()); \
extract_via_vlm(Path('../samples/attention-is-all-you-need.pdf'), d); \
print(json.dumps(json.loads((d/'content_list.json').read_text())[:8], indent=2))"
```
Expected: JSON blocks with headings (`text_level`), body `text`, some `equation`/`table`/`image` blocks; figure PNGs under `images/`.
- [ ] **Step 3:** Start the backend locally (`EXTRACTOR_PROVIDER=vlm`) + frontend, upload `attention-is-all-you-need.pdf`, confirm the reader shows structured chunks (headings/figures/equations), i.e. the content_list flows through `create_chunks_from_content_list` unchanged.
- [ ] **Step 4:** Note quality observations (tables/math) + approximate credit usage in the PR description; decide whether whole‑page figure fallback is needed.
- [ ] **Step 5: Open the PR** (runs backend CI, mocked tests only, no network):
```bash
git push -u origin feat/vlm-extractor
gh pr create --base main --title "feat(extraction): Qwen3-VL cloud extractor (EXTRACTOR_PROVIDER=vlm)" --body "Implements docs/superpowers/specs/2026-08-09-vlm-extraction-design.md. Off by default; see local verification notes."
```

---

## Self-Review

- **Spec coverage:** pluggable provider (Task 1,5) ✓; render (Task 2) ✓; per-page Qwen3‑VL call (Task 3) ✓; content_list assembly + assets + fallback (Task 4) ✓; dispatch/default-off (Task 5) ✓; mocked unit tests CI-safe + local integration (Tasks 3–6) ✓; config guardrails (Task 1) ✓.
- **Placeholders:** none: every code/test step has real content. Model tag `qwen3-vl:235b-cloud` is a documented default to confirm in Task 6 (spec risk).
- **Type consistency:** `render_pages`→`list[bytes]`; `call_vlm_page(png,model,client)->list[dict]`; `extract_via_vlm(pdf_path,output_dir)->Path`; `resolve_extractor(pdf_path,output_dir)->Path`: used consistently across Tasks 2–5.
