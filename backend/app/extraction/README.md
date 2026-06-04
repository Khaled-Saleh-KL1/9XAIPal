# Extraction Design

## Purpose

The `extraction` directory owns PDF-to-structure conversion through MinerU.

MinerU should extract structured markdown, headings, LaTeX math, tables, images, page metadata, and layout metadata. The output of extraction is an ordered list of chunks ready for database insertion.

## Files

### `pipeline.py`

Coordinates the full extraction workflow: receive PDF path, run MinerU, normalize output, extract assets, create ordered chunks, persist through ingestion services, and trigger embedding jobs.

### `mineru_client.py`

Wraps local MinerU execution, captures process output, locates generated artifacts, and reports structured extraction errors.

### `chunker.py`

Converts normalized MinerU output into structural chunks. It must preserve physical document order and assign monotonically increasing `sequence_id` values.

### `normalizer.py`

Cleans MinerU output into stable internal structures. It normalizes markdown, math blocks, image references, whitespace, and plain text for embeddings.

### `assets.py`

Moves extracted images and layout artifacts into local storage and returns metadata that can be persisted by the asset repository.

### `jobs.py`

Defines ingestion job state names and transition expectations. Expected states are `queued`, `extracting`, `chunking`, `embedding`, `complete`, and `failed`.

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
