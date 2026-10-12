# Extraction Design

## Purpose

The `extraction` directory owns PDF classification and conversion into ordered
structural chunks. The v3 path chooses the configured English extractor or the
Arabic printed/handwritten OCR route before shared chunking and profile processing.
See the [Arabic feature contract](../../../docs/05-features/16-arabic-document-routing.md).

MinerU should extract structured markdown, headings, LaTeX math, tables, images, page metadata, and layout metadata. The output of extraction is an ordered list of chunks ready for database insertion.

## Files

### `pipeline.py`

Async pipeline entry. The Celery execution path lives in `pipeline_sync.py`:
classify a PDF, select an extractor, normalize output, store chunks/assets, then
run the paper-fast or book-full profile. MinerU-specific repairs are provenance-gated.

### Arabic routing modules

`arabic_classifier.py` performs local text/vision language and style classification;
`arabic_types.py` owns decisions, extractor provenance and typed routing failures.
`gemini_ocr_client.py` implements gateway/direct key attempts with separate printed
and handwritten models. `arabic_ocr.py` validates page boundaries and publishes
artifacts; `arabic_fallback.py` and `arabic_repair.py` own Gemma recovery/repair;
`arabic_adapter.py` converts OCR output into shared structural chunks.
`arabic_capability.py` probes direct printed-Flash access at enabled-OCR startup.
See the [operator runbook](../../../docs/runbooks/arabic-document-ocr.md) for
defaults, credentials, provider recovery and the manual-handwriting limit.

### `mineru_client.py`

Wraps local MinerU execution, captures process output, locates generated artifacts, and reports structured extraction errors.

### `chunker.py`

Converts normalized MinerU output into structural chunks. It must preserve physical document order and assign monotonically increasing `sequence_id` values.

### `normalizer.py`

Cleans MinerU output into stable internal structures. It normalizes markdown, math blocks, image references, whitespace, and plain text for embeddings.

### `assets.py`

Moves extracted images and layout artifacts into local storage and returns metadata that can be persisted by the asset repository.

### `jobs.py`

Defines ingestion job state names and transition expectations. Expected stages include
`queued`, `extracting`, `chunking`, `embedding`, `summarizing`, `complete`, and `failed`.

## Chunking Rules

- Preserve the physical order of the PDF.
- Assign sequence IDs starting at `1` per document.
- Keep math blocks intact.
- Attach images to nearby structural chunks.
- Preserve heading paths, page spans, and bounding boxes where available.

## Data Dependencies

`extraction` depends on `core.paths` for storage, `services.ingestion` for persistence, and `embeddings.service` after chunks are stored.

The sequence ID is created by `extraction.chunker` and persisted transactionally by `services.ingestion` into PostgreSQL.

Embedding payloads are generated after chunk persistence by `embeddings.service` and stored in PostgreSQL through `database.repositories.embeddings` and `database.pgvector`.
