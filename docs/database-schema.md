# Database Schema

Canonical schema is in [database/schema.sql](../backend/app/database/schema.sql).
It is applied on startup by [database/migrations.py](../backend/app/database/migrations.py)
(idempotent `CREATE TABLE IF NOT EXISTS …`).

Two Postgres extensions are required: `vector` (pgvector) and `uuid-ossp`.

## ERD

```
documents (1) ─────< (N) chunks ─────────< (1) chunk_embeddings
                        │ \───< (N) chunk_assets
                        │ \───< (N) figure_descriptions
                        ▲
                        │
            conversation_turns ──< (1) ask_traces
            conversation_turns ── parent_turn_id → conversation_turns.id (sub-threads)

documents (1) ─────< (N) ingestion_jobs
documents (1) ─────< (N) section_summaries
documents (1) ─────< (N) figure_descriptions
```

All FKs use `ON DELETE CASCADE` except `conversation_turns.document_id`
which uses `SET NULL` (so a chat about a deleted paper survives).

## Tables

### `documents`

The library row.

| Column                      | Type        | Notes                                                 |
| --------------------------- | ----------- | ----------------------------------------------------- |
| `id`                        | `UUID`      | PK, server-generated.                                 |
| `filename`                  | `TEXT`      | The opaque `<uuid>.pdf` on disk under `documents/`.   |
| `original_filename`         | `TEXT`      | What the user uploaded (used by `/raw`).              |
| `file_size_bytes`           | `BIGINT`    |                                                       |
| `page_count`                | `INTEGER`   | Set by `pypdf` after pipeline completes.              |
| `status`                    | `TEXT`      | `queued / complete / failed`.                         |
| `error_message`             | `TEXT`      | Last failure message.                                 |
| `reading_order`             | `JSONB`     | LLM-corrected sequence of chunk sequence_ids.         |
| `reading_order_model`       | `TEXT`      |                                                       |
| `reading_order_updated_at`  | `TIMESTAMPTZ` |                                                     |
| `extractor`                 | `TEXT`      | `mineru` or `pymupdf_fallback`.                      |
| `created_at`                | `TIMESTAMPTZ` | `DEFAULT NOW()`.                                    |
| `updated_at`                | `TIMESTAMPTZ` | Bumped by `update_document_status`.                 |

### `chunks`

One row per structural unit (heading, paragraph, math, table, figure).

| Column               | Type       | Notes                                       |
| -------------------- | ---------- | ------------------------------------------- |
| `id`                 | `UUID`     | PK.                                         |
| `document_id`        | `UUID`     | FK → `documents.id`, cascade delete.        |
| `sequence_id`        | `INTEGER`  | 1-based reading order within the document.  |
| `parent_sequence_id` | `INTEGER`  | Reserved for nested structures.             |
| `chunk_type`         | `TEXT`     | `text / heading / math / table / figure / footnote`. |
| `heading_path`       | `TEXT[]`   | Breadcrumb from H1 to current heading.      |
| `markdown`           | `TEXT`     | Normalized markdown body.                   |
| `plain_text`         | `TEXT`     | What we embed.                              |
| `page_start`         | `INTEGER`  | Currently nullable.                         |
| `page_end`           | `INTEGER`  | Currently nullable.                         |
| `bbox_json`          | `JSONB`    | Reserved for bounding boxes.                |
| `token_count`        | `INTEGER`  | `≈ len(plain_text) / 4`.                    |
| `table_json`         | `JSONB`    | Structured table data for `chunk_type='table'`. |
| `created_at`         | `TIMESTAMPTZ` |                                          |

Unique constraint: `(document_id, sequence_id)`.
Index: `idx_chunks_document_sequence(document_id, sequence_id)`.

### `chunk_embeddings`

A 1:1 sidecar to `chunks`. Separate table so heavy embedding rows can be
loaded only when needed.

| Column            | Type           | Notes                                |
| ----------------- | -------------- | ------------------------------------ |
| `chunk_id`        | `UUID`         | PK, FK → `chunks.id`, cascade.       |
| `embedding`       | `vector(768)`  | Dimension matches `vector_dimension`. |
| `embedding_model` | `TEXT`         | Name of the embedding model used.    |
| `created_at`      | `TIMESTAMPTZ`  |                                      |

Cosine search: `ORDER BY embedding <=> :query_embedding`.

### `chunk_assets`

Images extracted from MinerU output, linked back to the chunk that
referenced them.

| Column       | Type       | Notes                                           |
| ------------ | ---------- | ----------------------------------------------- |
| `id`         | `UUID`     | PK.                                             |
| `chunk_id`   | `UUID`     | FK → `chunks.id`, cascade.                      |
| `asset_type` | `TEXT`     | `image`, etc.                                   |
| `file_path`  | `TEXT`     | **Relative** to `images_dir()`. Served at `/static/images/<file_path>`. |
| `mime_type`  | `TEXT`     |                                                 |
| `width`      | `INTEGER`  | Currently null.                                 |
| `height`     | `INTEGER`  | Currently null.                                 |
| `caption`    | `TEXT`     | Currently null.                                 |
| `created_at` | `TIMESTAMPTZ` |                                              |

Index: `idx_chunk_assets_chunk_id(chunk_id)`.

### `conversation_turns`

The append-only chat log.

| Column            | Type          | Notes                                            |
| ----------------- | ------------- | ------------------------------------------------ |
| `id`              | `UUID`        | PK.                                              |
| `conversation_id` | `UUID`        | Groups turns into a thread.                      |
| `document_id`     | `UUID` (null) | FK → `documents.id`, **`SET NULL`** on delete.   |
| `parent_turn_id`  | `UUID` (null) | FK → `conversation_turns.id` cascade (sub-threads). |
| `role`            | `TEXT`        | `user / assistant / compaction`.                 |
| `content`         | `TEXT`        | The prompt or the answer.                        |
| `context_type`    | `TEXT`        | `LOCAL / GLOBAL / OVERVIEW / EXTERNAL / OUT_OF_SCOPE / COMPACTION`. |
| `router_reason`   | `TEXT`        | Why the router picked this context.              |
| `model`           | `TEXT`        | The actual model name the LLM returned.          |
| `citations`       | `JSONB`       | JSON-serialized list of `Citation` dicts.        |
| `created_at`      | `TIMESTAMPTZ` |                                                  |

Index: `idx_conversation_turns_conversation(conversation_id, created_at)`.

### `ask_traces`

Per-call telemetry attached to the assistant turn.

| Column                  | Type          | Notes                                           |
| ----------------------- | ------------- | ----------------------------------------------- |
| `id`                    | `UUID`        | PK.                                             |
| `conversation_turn_id`  | `UUID`        | FK → `conversation_turns.id`, cascade.          |
| `context_type`          | `TEXT`        |                                                 |
| `router_reason`         | `TEXT`        |                                                 |
| `retrieved_chunk_ids`   | `UUID[]`      | Currently always null — reserved.               |
| `model`                 | `TEXT`        |                                                 |
| `prompt_tokens`         | `INTEGER`     | From Ollama.                                    |
| `completion_tokens`     | `INTEGER`     |                                                 |
| `latency_ms`            | `INTEGER`     | Wall-clock time inside `handle_ask`.            |
| `created_at`            | `TIMESTAMPTZ` |                                                 |

### `ingestion_jobs`

One row per upload; tracks the pipeline state machine.

| Column          | Type          | Notes                                                  |
| --------------- | ------------- | ------------------------------------------------------ |
| `id`            | `UUID`        | PK.                                                    |
| `document_id`   | `UUID`        | FK → `documents.id`, cascade.                          |
| `status`        | `TEXT`        | `queued / extracting / chunking / embedding / summarizing / complete / failed`. |
| `error_message` | `TEXT`        |                                                        |
| `started_at`    | `TIMESTAMPTZ` | Set on first non-queued transition (idempotent).       |
| `completed_at`  | `TIMESTAMPTZ` | Set on `complete` or `failed`.                         |
| `created_at`    | `TIMESTAMPTZ` |                                                        |

Index: `idx_ingestion_jobs_status(status)`.

### `section_summaries`

Pre-computed hierarchical overviews used by the OVERVIEW chat route.

| Column                | Type          | Notes                                             |
| --------------------- | ------------- | ------------------------------------------------- |
| `id`                  | `UUID`        | PK.                                               |
| `document_id`         | `UUID`        | FK → `documents.id` cascade.                      |
| `section_id`          | `TEXT`        | Stable ID (e.g. `h1-03-introduction`).            |
| `level`               | `INTEGER`     | `0` = whole paper, `1` = H1, `2` = H2.            |
| `heading_path`        | `TEXT[]`      | Heading breadcrumb.                               |
| `sequence_start`      | `INTEGER`     | Inclusive source sequence range.                  |
| `sequence_end`        | `INTEGER`     |                                                    |
| `summary_markdown`    | `TEXT`        | LLM-generated summary.                            |
| `summary_plain`       | `TEXT`        | Plain-text version.                               |
| `source_chunk_ids`    | `UUID[]`      | Chunk IDs fed to the LLM (citations).             |
| `model`               | `TEXT`        |                                                    |
| `prompt_hash`         | `TEXT`        | Hash of prompt template + version.                |
| `created_at`          | `TIMESTAMPTZ` |                                                    |

`UNIQUE(document_id, section_id, model)`.

### `figure_descriptions`

VLM-generated technical descriptions of figures/diagrams.

| Column                       | Type          | Notes                                    |
| ---------------------------- | ------------- | ---------------------------------------- |
| `id`                         | `UUID`        | PK.                                      |
| `document_id`                | `UUID`        | FK → `documents.id` cascade.             |
| `chunk_id`                   | `UUID`        | FK → `chunks.id` cascade.                |
| `image_path`                 | `TEXT`        | Relative path under `images/`.           |
| `description_markdown`       | `TEXT`        | VLM-generated description.               |
| `description_plain`          | `TEXT`        | Plain-text version.                      |
| `source_sequence_start`      | `INTEGER`     |                                          |
| `source_sequence_end`        | `INTEGER`     |                                          |
| `referenced_by_chunk_ids`    | `UUID[]`      | Text chunks that mention this figure.    |
| `model`                      | `TEXT`        | eg. `qwen3.5:cloud`.                    |
| `prompt_hash`                | `TEXT`        |                                          |
| `created_at`                 | `TIMESTAMPTZ` |                                          |

`UNIQUE(chunk_id, model)`.

## Status state machines

**`documents.status`**

```
queued ──► complete
      └──► failed
```

**`ingestion_jobs.status`**

```
queued → extracting → chunking → embedding → summarizing → complete
                   └───────────────────────────────────► failed
```