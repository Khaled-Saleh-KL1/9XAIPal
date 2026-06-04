# Database Migrations & Schema Changes (Personal Use)

This project uses an "apply the SQL" approach rather than Alembic because it is a single-tenant local desktop tool.

## After pulling latest code (especially after section summarization feature)

1. Make sure your Postgres container is running:
   ```bash
   docker compose up -d db
   ```

2. Apply the new schema additions (idempotent):
   ```bash
   docker compose exec -T db psql -U postgres -d 9xaipal -f /docker-entrypoint-initdb.d/schema.sql
   ```
   Or from host (if you have psql locally and port 5432 exposed):
   ```bash
   psql -h localhost -U postgres -d 9xaipal -f backend/app/database/schema.sql
   ```

   The `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` statements are safe to run multiple times.

## New table: section_summaries

See the detailed comment block at the bottom of `backend/app/database/schema.sql`.

This table is **completely independent** of the `chunks` + `chunk_embeddings` tables. Deleting a document cascades correctly.

## LLM Reading Order Reconstruction for Two-Column Papers (new)

Add these columns to support AI-corrected reading order:

```bash
docker compose exec -T db psql -U postgres -d 9xaipal -f /docker-entrypoint-initdb.d/schema.sql
```

New fields on `documents`:
- `reading_order` (JSONB): ordered list of original `sequence_id`s in correct reading order
- `reading_order_model`
- `reading_order_updated_at`

You can trigger reconstruction from the UI (ReadingView) with the "Reconstruct Reading Order (AI)" button. This sends chunks + bboxes per page to the LLM (gemma4:26b) to intelligently reorder text from two-column layouts and fix cross-page continuations.

## Rich Table Extraction + Figure/Architecture VLM Descriptions (2026 updates)

These are major quality improvements for deep interaction with tables, diagrams, and architectures.

**Apply the schema** (same command as above):
```bash
docker compose exec -T db psql -U postgres -d 9xaipal -f /docker-entrypoint-initdb.d/schema.sql
```

New capabilities added:
- `chunks.table_json` (JSONB) — structured table data (headers + rows) for `chunk_type = 'table'`.
- `figure_descriptions` table — rich, technical VLM-generated descriptions of figures/diagrams (especially architectures). These are generated during/after the normal ingestion + summarization pass.

These descriptions are stored with full attribution so they participate in GLOBAL search, OVERVIEW synthesis, and targeted "explain this figure" flows.

To re-generate figure descriptions for an existing paper (after prompt/model improvements):
Use the existing `/papers/{id}/regenerate-summaries` endpoint (it will be extended to also refresh figure descriptions).

## When you change embedding or chat model

After a paper reaches `status = 'complete'` (embedding finished), the system will automatically fire the `generate_section_summaries` Celery task.

To force re-generation for one paper (e.g. after prompt improvements):

```bash
# From the backend container or with proper PYTHONPATH
python -c '
from app.workers.tasks import generate_section_summaries
generate_section_summaries.delay("your-document-uuid-here")
'
```

Or use the upcoming API endpoint `POST /papers/{paper_id}/regenerate-summaries`.

## Quality note (for the author)

This feature was implemented because you explicitly said you are willing to wait 5-15 minutes per paper for higher-quality outputs. The section summarizer uses a rich prompt tuned for scientific papers and preserves source attribution for citations.

Enjoy your personal research assistant.

## Nested Sub-Threads for Tangents (paper-free focus mode)

Added support for arbitrary-depth nested sub-threads so long tangents (transduction → CNN/RNN formulas → history, etc.) never pollute the main paper discussion.

**Exact schema change (one column only):**

```sql
ALTER TABLE conversation_turns 
ADD COLUMN IF NOT EXISTS parent_turn_id UUID 
    REFERENCES conversation_turns(id) 
    ON DELETE CASCADE;
```

- `parent_turn_id IS NULL` → turn belongs to the main linear chat for its `conversation_id`.
- Non-NULL → the turn is part of a sub-thread. The root of a sub-thread is the original user message that started the tangent (that user message itself keeps `parent_turn_id = NULL` so it stays permanently visible and clickable in the main view).

**Apply the migration (idempotent):**

```bash
docker compose exec -T db psql -U postgres -d 9xaipal -f /docker-entrypoint-initdb.d/schema.sql
```

Or from host:
```bash
psql -h localhost -U postgres -d 9xaipal -f backend/app/database/schema.sql
```

**New repository helpers** (see `backend/app/database/repositories/conversations.py`):
- `get_main_chat(conversation_id)`
- `get_thread_subtree(root_turn_id)` — uses recursive CTE + special-case logic to include the original first AI reply
- `has_children(turn_id)`
- `get_thread_message_count(root_turn_id)`

Compaction, orchestrator context routing, and the UI are now fully thread-aware. Sub-threads run in paper-free mode by default.

All previous conversations (created before this feature) continue to work unchanged because they have `parent_turn_id = NULL`.
