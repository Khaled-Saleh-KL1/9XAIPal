# Ingestion Pipeline

This is the path a PDF takes from "the user dragged it onto the library"
to "I can read it chunk-by-chunk and ask grounded questions about it."

## End-to-end timeline

```
[Client]                  [API]                      [Celery worker]
─────────                 ─────                      ────────────────
drag/drop PDF
   │
   ▼
POST /papers/upload ──►  write to documents/<uuid>.pdf
                         write to assets/<doc_id>.pdf (for /raw download)
                         INSERT documents      (status='queued')
                         INSERT ingestion_jobs (status='queued')
                         process_ingestion.delay(doc_id, job_id, filename)
                         ◄── 201 {id, status:'processing'}
   │
   ▼
poll /papers/{id}/progress every 1s
                                                    run_pipeline_sync()
                                                      │
                                                      ▼
                                                    UPDATE ingestion_jobs → 'extracting'
                                                    mineru -p ... -o extracted/<doc_id>
                                                      → writes extracted/<doc_id>/*.md + images
                                                      │
                                                      ▼
                                                    UPDATE ingestion_jobs → 'chunking'
                                                    parse content_list.json into structural chunks
                                                    INSERT chunks (one row per chunk)
                                                      │
                                                      ▼
                                                    move images to images/<doc_id>/
                                                    INSERT chunk_assets (link via markdown ref)
                                                      │
                                                      ▼
                                                    UPDATE ingestion_jobs → 'embedding'
                                                    embed_document.delay(doc_id)
                                                    UPDATE ingestion_jobs → 'complete'
                                                    UPDATE documents      → 'complete', page_count
   │                                                  │
   ▼                                                  ▼
status == 'complete'                             embed_document_chunks_sync()
   │                                                → batches of 20 chunks
   ▼                                                → INSERT chunk_embeddings (vector(VECTOR_DIMENSION))
switch to ReadingView                               → on completion: generate_section_summaries.delay()

                                                    generate_section_summaries
                                                      → hierarchical summaries → section_summaries
                                                      → VLM figure descriptions → figure_descriptions
```

## Step 1 — Upload

```http
POST /api/v1/papers/upload
Content-Type: multipart/form-data
file: <PDF bytes>
```

Server:

1. Generates a fresh storage filename: `<uuid4().hex>.pdf`.
2. Reads the file body into memory.
3. Writes it to `<storage_root>/documents/<uuid>.pdf` — this is what MinerU consumes.
4. Inserts a row into `documents` with `status='queued'`.
5. Writes a second copy to `<storage_root>/assets/<doc_id>.pdf`.
6. Inserts a row into `ingestion_jobs` with `status='queued'`.
7. Dispatches `process_ingestion.delay(doc_id, job_id, filename)` to Celery.
8. Returns `201` immediately.

The frontend starts a 1-second poll against `/progress`, showing the `ProcessingOverlay`.

## Step 2 — MinerU extraction

`process_ingestion` calls `run_pipeline_sync`:

1. `UPDATE ingestion_jobs SET status='extracting'`.
2. `mineru -p documents/<uuid>.pdf -o extracted/<doc_id> -m auto`.
3. MinerU writes one or more `.md` files and asset images.
4. `find_markdown_output` picks the largest `.md` file.
5. `find_images` recursively collects every image file.

If `mineru` exits non-zero, the pipeline raises `MinerUError`, the job
+ document are marked `failed`, and the polling frontend exits to the
library.

## Step 3 — Chunking

The chunker is **structural**: a chunk is one heading, one paragraph, one
math block, one table, or one figure.

Implementation:

1. Parse MinerU's `content_list.json` for structure. Fall back to regex
   markdown chunking if that's unavailable.
2. For each section:
   - Assign a monotonically increasing `sequence_id` (1-based).
   - Detect the chunk type: `heading > math ($$…$$) > table (|…|…|) > figure (![…](…)) > text`.
   - Maintain `current_heading_path` — a breadcrumb of H1→H6 titles.
   - Extract any `![alt](src)` image filenames into `image_refs`.
   - Extract `table_json` for table chunks.
   - Normalize markdown and extract plain text for embedding.
3. Returns a list of dicts ready for persistence.

## Step 4 — Persisting chunks + images

1. `UPDATE ingestion_jobs SET status='chunking'`.
2. `store_chunks` inserts one row per chunk into `chunks`.
3. For every image found in MinerU's output, call `move_asset_to_storage`.
   Copies the file to `images/<doc_id>/<uuid>.<ext>` and returns metadata.
4. Build an `original_name → asset_meta` map.
5. For each persisted chunk, look up its `image_refs` against the map.
   Each hit becomes an `INSERT INTO chunk_assets`.

## Step 5 — Embedding

1. `UPDATE ingestion_jobs SET status='embedding'`.
2. `embed_document.delay(document_id)` dispatches to Celery.
3. `UPDATE ingestion_jobs SET status='complete'`.
4. `UPDATE documents SET status='complete', page_count=<pypdf count>`.

The Celery worker:

1. Opens its own DB session.
2. Calls `embed_document_chunks_sync(session, document_id, batch_size=20)`.
3. Loops fetching chunks with no `chunk_embeddings` row.
4. Sends `plain_text` in batches to Ollama's embedding API.
5. Inserts each result into `chunk_embeddings` (`vector(VECTOR_DIMENSION)`, default 1024) along with the resolved `embedding_model` name.
6. Commits after each batch.

## Step 6 — Summarization (background)

After embeddings are done, `generate_section_summaries.delay()` fires:

1. Hierarchical section summarization (level 0 = paper, level 1 = H1, level 2 = H2).
2. VLM figure descriptions for every `chunk_type='figure'` chunk.
3. Results stored in `section_summaries` and `figure_descriptions` tables.

This step is slow (minutes per paper) but doesn't block the user — they
can start reading and asking questions as soon as `status='complete'`.

## Status taxonomy

| Job status                    | Doc status   | Frontend behavior |
| ----------------------------- | ------------ | ----------------- |
| `queued`                      | `queued`     | overlay: queued   |
| `extracting`                  | `queued`     | overlay: extracting |
| `chunking`                    | `queued`     | overlay: chunking |
| `embedding`                   | `queued`     | overlay: embedding |
| `summarizing`                 | `complete`   | overlay closes    |
| `complete`                    | `complete`   | flip to ReadingView |
| `failed`                      | `failed`     | back to LibraryView |

## Deletion

`DELETE /papers/{id}` removes:
- DB cascade: chunks, chunk_embeddings, chunk_assets, ingestion_jobs,
  section_summaries, figure_descriptions.
- Disk cleanup (best effort): `documents/<filename>`, `assets/<id>.pdf`,
  `extracted/<id>/`, `images/<id>/`.
- Conversation turns survive with `document_id` set to null.