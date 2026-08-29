# Design: Cloud VLM PDF Extraction (Qwen3‑VL via Ollama Cloud)

Date: 2026-08-09
Status: Draft for review

## Goal

Replace the heavy, local **MinerU** PDF‑extraction step with an equivalent path
that calls a **cloud vision model (Qwen3‑VL) via Ollama Cloud**, so the backend
can run on a cheap host (no `torch`/MinerU/GPU) while keeping high‑quality
structural extraction. This is **sub‑project A**. Hosting the light backend 24/7
(**sub‑project B**) is a separate spec that depends on this one.

Non‑goals: changing chunking, embeddings, retrieval, reading modes, or the
frontend. Removing MinerU entirely (it stays available behind a config switch).

## The contract (the seam we build to)

The entire downstream pipeline consumes exactly one artifact:
`create_chunks_from_content_list(content_list_path)` reads MinerU's
**`content_list.json`**: a JSON array of typed blocks. If the VLM extractor
emits a compatible `content_list.json` (plus referenced asset images), nothing
downstream changes.

Required block schema (as consumed by `chunker.py`):

| `type` | key fields | notes |
|---|---|---|
| `text` | `text`, optional `text_level` (1/2/3…) | `text_level` present ⇒ heading |
| `equation` | `text` = `"$$ … $$"` (optional `\tag{N}`), optional `img_path` | LaTeX body |
| `image` / `chart` | `img_path`, `img_caption` / `chart_caption` (list) | figure crop saved as asset |
| `table` | `table_body` (HTML `<table>…`), `table_caption` (list), optional `img_path` | HTML preferred |
| `list` | `list_items` (array) or `text` | rendered as text |
| `page_footnote` / `aside_text` | `text` | → footnote chunk |
| `header` / `footer` / `page_number` | n/a | dropped as chrome |

Every block carries `page_idx` (**0‑indexed**; the chunker converts to 1‑indexed).
Blocks appear in **reading order**. Asset images referenced by `img_path` live in
`<output_dir>/images/<name>` (basename is what the chunker keys on); `assets.py`
moves them into storage.

## Architecture

Pluggable extractor, mirroring the existing `LLM_PROVIDER` pattern:

- New setting **`EXTRACTOR_PROVIDER = mineru | vlm | pymupdf`** (default stays
  `mineru` so nothing changes until opted in; hosted deploy sets `vlm`).
- `extract_pdf_sync()` (currently MinerU‑only) becomes a dispatcher that routes to
  the MinerU client, the new VLM client, or the PyMuPDF fallback.
- New module **`app/extraction/vlm_client.py`** produces a MinerU‑compatible
  `output_dir` (`content_list.json` + `images/`), so `run_pipeline_sync` is
  unchanged apart from the dispatch call.

### `vlm_client.py` components

1. **Page render**: `fitz` (PyMuPDF, already a dependency): each PDF page →
   PNG at a configurable DPI (default ~150–200). No new deps.
2. **Per‑page VLM call**: reuse the Ollama Cloud client pattern
   (`_ollama_headers()` Bearer auth + httpx) against `OLLAMA_BASE_URL`
   (`https://ollama.com`) using `EXTRACTOR_VLM_MODEL` (default: largest Qwen3‑VL
   cloud tag, e.g. `qwen3-vl:<largest>-cloud`; exact tag confirmed at build time).
   Sends the page image + a strict prompt requesting a JSON array of blocks in the
   schema above, including **bounding boxes for figures** so we can crop them.
3. **Parse + validate**: parse the model's JSON (tolerant: strip code fences,
   validate against the schema, drop malformed blocks). Assign `page_idx`.
4. **Assets**: for each figure/chart block, crop the page PNG at the returned
   bbox and save to `images/`; set `img_path`. Equations render from LaTeX (crop
   optional); tables render from HTML (crop optional).
5. **Assemble**: concatenate per‑page blocks in page then reading order → write
   `content_list.json`.
6. **Fallback**: on VLM/parse failure for a page (after retries), fall back to
   PyMuPDF text for that page so a single bad page never fails the whole doc.

## Configuration (all with safe defaults)

- `EXTRACTOR_PROVIDER` (default `mineru`)
- `EXTRACTOR_VLM_MODEL` (default largest Qwen3‑VL cloud tag)
- `EXTRACTOR_VLM_DPI` (default 180)
- `EXTRACTOR_VLM_MAX_PAGES` (default 0 = unlimited; guardrail against runaway credit use)
- `EXTRACTOR_VLM_CONCURRENCY` (default small, e.g. 2–4; respects Ollama Cloud rate limits)
- Reuses existing `OLLAMA_BASE_URL` / `OLLAMA_API_KEY`.

## Data flow

`upload → Celery task → run_pipeline_sync → extract_pdf_sync (EXTRACTOR_PROVIDER=vlm)
→ vlm_client: render pages → Qwen3‑VL per page → content_list.json + images/
→ create_chunks_from_content_list → embeddings (Ollama Cloud) → DB`

Runs inside the existing Celery worker, so per‑page latency is hidden from the UI
(the progress overlay already shows extract → chunk → embed steps).

## Error handling / cost control

- Per‑page retry with backoff on 429/5xx; bounded concurrency.
- Per‑page fallback to PyMuPDF text on persistent failure.
- `EXTRACTOR_VLM_MAX_PAGES` guardrail; log estimated page count before starting.
- Never crash the pipeline on one bad block: validate and drop.

## Testing

- **Unit test** (matches existing style that mocks LLM calls): feed a canned
  Qwen3‑VL JSON response, assert `vlm_client` writes a valid `content_list.json`
  and that `create_chunks_from_content_list` produces the expected typed chunks.
- **Local integration**: run against `samples/attention-is-all-you-need.pdf` and
  `samples/resnet-1512.03385.pdf` with a real key; eyeball the `content_list` and
  the reader output for headings/figures/equations/tables.
- Backend CI stays green (the new path is behind `EXTRACTOR_PROVIDER`, default
  `mineru`; the mocked unit test runs without network).

## Risks / open questions

- **Table/math precision** vs MinerU: accepted as best‑effort for v1; falls back
  to plain text when the model's HTML/LaTeX is malformed.
- **Credit usage**: many‑page books are token‑heavy; `EXTRACTOR_VLM_MAX_PAGES`
  and the async worker mitigate; revisit if credits run low.
- **Bounding‑box quality** for figure crops: if Qwen3‑VL bboxes are unreliable,
  fall back to whole‑page image for figure‑heavy pages (decide during local test).
- **Exact Qwen3‑VL cloud tag + image API shape**: confirm against Ollama Cloud
  docs during build (Ollama `/api/chat` `images` field vs base64 inline).

## Out of scope (→ sub‑project B, separate spec)

Hosting the light backend 24/7: cheap VPS running the docker‑compose stack minus
MinerU/torch, managed Postgres+pgvector and Redis (or in‑box), persistent storage
for `app/storage`, secrets, and wiring the Vercel frontend via `VITE_API_BASE_URL`
(+ CORS) or a Vercel `/api` rewrite.
