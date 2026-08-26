# Database migrations & schema changes

> **What this is:** how schema changes reach a running database, and what to do when they don't.
>
> **Owns:** the migration procedure.
> **Does not own:** what the tables contain ([database-schema.md](database-schema.md)).
>
> **Status:** current · **Last verified:** the rename runbook 2026-08-26 (written against the
> live containers, **not** executed here — see its own warning); the rest 2026-07-25 against
> [`database/migrations.py`](../../backend/app/database/migrations.py) (`main`, 9b75500)
> **Verify with:** restart the API and read the migration log lines
>
> ⚠ **Migrations are best-effort by design.** Each statement runs in its own transaction and a
> failure is logged as a warning, not raised — then `_ensure_recent_columns()` patches up columns
> that didn't apply. This is a recovery mechanism, not a migration system: it cannot detect a
> partially-applied change it doesn't already know about. Replacing it with Alembic is tracked in
> [roadmap.md](../roadmap.md).

This project uses an "apply the SQL" approach rather than Alembic because it is a single-tenant local desktop tool.

---

## One-time: the 9XAIPal → ScholarFlow rename (2026-08-26)

⚠ **A library that already exists does not follow the rename by itself.** The repo now says
`scholarflow` everywhere, including the compose **volume keys** and the `POSTGRES_*` defaults. A
Docker named volume cannot be renamed in place, so the next `docker compose up` after pulling this
change creates **empty** volumes and the app comes up with an empty library. The papers are not
lost — they are still in `backend_9xaipal_postgres_data` — but nothing points at them.

Do this once, with the stack stopped. It takes about a minute and leaves the old volumes untouched
as a backup.

```bash
cd backend

# 1. Rename the role and the databases, while the OLD names are still in .env.
#    ⚠ Run this BEFORE stopping postgres — the rename happens inside a running server.
docker compose stop api celery_worker autoheal
docker exec -i 9xaipal-postgres psql -U 9xaipal -d postgres -v ON_ERROR_STOP=1 <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname LIKE '9xaipal%' AND pid <> pg_backend_pid();
ALTER DATABASE "9xaipal" RENAME TO scholarflow;
ALTER DATABASE "9xaipal_test" RENAME TO scholarflow_test;   -- skip if you never ran the tests
ALTER ROLE "9xaipal" RENAME TO scholarflow;
ALTER ROLE scholarflow WITH PASSWORD 'scholarflow_dev_password';
SQL

# 2. Stop everything and copy the volumes to their new names.
docker compose down
for v in postgres redis; do
  docker volume create "backend_scholarflow_${v}_data"
  docker run --rm \
    -v "backend_9xaipal_${v}_data:/from" \
    -v "backend_scholarflow_${v}_data:/to" \
    alpine sh -c 'cd /from && cp -a . /to'
done

# 3. Point .env at the new names (it is gitignored, so the rename did not touch it).
sed -i '' 's/9xaipal/scholarflow/g' .env    # GNU sed: drop the ''

# 4. Bring it back up and check the library is all there.
docker compose up -d
curl -s localhost:${API_PORT:-8000}/api/v1/papers | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["documents"]),"papers")'
```

⚠ **`ALTER ROLE … RENAME` and the password.** Postgres invalidates an `md5`-hashed password when
the role is renamed, because the username is part of the hash. `scram-sha-256` (the PG14+ default,
and what the `pgvector/pgvector:pg16` image uses) does not — but step 1 resets it anyway so the
outcome does not depend on which one your volume was initialised with.

⚠ **Keep `backend_9xaipal_*_data` until you have opened the app and seen your papers.** They are
the only copy of the pre-rename state. Once you are satisfied:
`docker volume rm backend_9xaipal_postgres_data backend_9xaipal_redis_data`.

**Starting fresh instead?** Nothing to do — a new install creates `scholarflow` everywhere.

## After pulling latest code (especially after section summarization feature)

1. Make sure your Postgres container is running:
   ```bash
   docker compose up -d db
   ```

2. Apply the new schema additions (idempotent):
   ```bash
   docker compose exec -T db psql -U postgres -d scholarflow -f /docker-entrypoint-initdb.d/schema.sql
   ```
   Or from host (if you have psql locally and port 5432 exposed):
   ```bash
   psql -h localhost -U postgres -d scholarflow -f backend/app/database/schema.sql
   ```

   The `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` statements are safe to run multiple times.

## New table: section_summaries

See the detailed comment block at the bottom of `backend/app/database/schema.sql`.

This table is **completely independent** of the `chunks` + `chunk_embeddings` tables. Deleting a document cascades correctly.

## LLM Reading Order Reconstruction for Two-Column Papers (new)

Add these columns to support AI-corrected reading order:

```bash
docker compose exec -T db psql -U postgres -d scholarflow -f /docker-entrypoint-initdb.d/schema.sql
```

New fields on `documents`:
- `reading_order` (JSONB): ordered list of original `sequence_id`s in correct reading order
- `reading_order_model`
- `reading_order_updated_at`

You can trigger reconstruction from the UI (ReadingView) with the "Reconstruct Reading Order (AI)" button. This sends chunks + bboxes per page to the resolved chat model to intelligently reorder text from two-column layouts and fix cross-page continuations.

## Rich Table Extraction + Figure/Architecture VLM Descriptions (2026 updates)

These are major quality improvements for deep interaction with tables, diagrams, and architectures.

**Apply the schema** (same command as above):
```bash
docker compose exec -T db psql -U postgres -d scholarflow -f /docker-entrypoint-initdb.d/schema.sql
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
docker compose exec -T db psql -U postgres -d scholarflow -f /docker-entrypoint-initdb.d/schema.sql
```

Or from host:
```bash
psql -h localhost -U postgres -d scholarflow -f backend/app/database/schema.sql
```

**New repository helpers** (see `backend/app/database/repositories/conversations.py`):
- `get_main_chat(conversation_id)`
- `get_thread_subtree(root_turn_id)` — uses recursive CTE + special-case logic to include the original first AI reply
- `has_children(turn_id)`
- `get_thread_message_count(root_turn_id)`

Compaction, orchestrator context routing, and the UI are now fully thread-aware. Sub-threads run in paper-free mode by default.

All previous conversations (created before this feature) continue to work unchanged because they have `parent_turn_id = NULL`.

## Margin notes (`paper_notes`)

Added with the article reader. Columns are in
[database-schema.md](database-schema.md#paper_notes); this section covers only how the change
lands.

The table itself is created by `schema.sql` on startup (`CREATE TABLE IF NOT EXISTS`). Two columns
were added after it first shipped, so they also live in `_ensure_recent_columns()`:

```sql
ALTER TABLE paper_notes ADD COLUMN IF NOT EXISTS margin_side TEXT NOT NULL DEFAULT 'right';
ALTER TABLE paper_notes ADD COLUMN IF NOT EXISTS requested_model TEXT;
```

⚠ **The migration runner splits `schema.sql` on `;`.** A semicolon inside a SQL comment therefore
truncates the statement mid-definition, and the table silently fails to create — the failure is a
logged warning, not an error, so the first symptom is a 500 at runtime. This bit during
development. Keep semicolons out of comments in `schema.sql`.

Nothing needs re-ingesting: existing papers gain the table empty, and every reader action that
writes a note tolerates a `NULL` `anchor_chunk_id`.

## Equation crops on `math` chunks

No schema change. The chunker now records MinerU's cropped equation bitmap in `image_refs` for
`equation` entries, so `chunk_assets` gains rows for `math` chunks on the next ingest.

⚠ Existing papers do **not** gain equation crops until re-chunked
(`POST /papers/{id}/rechunk`) — cheap, since it reuses the cached MinerU output and never re-runs
extraction. The same re-chunk also applies the U+FFFD glyph repair described in
[ingestion-pipeline.md](../02-architecture/ingestion-pipeline.md).
