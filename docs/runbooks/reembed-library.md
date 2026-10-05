# Re-embed the embedded library safely

> **Status:** Source-checked 2026-10-05 (48cb8c6). This runbook documents the launcher; no database
> or running service was contacted for this documentation update.

Use this procedure to rebuild stored chunk embeddings for documents already marked as embedded.
The launcher selects documents with at least one chunk and queues the existing Celery
embed_document task in forced repair mode. The task upserts vectors by chunk ID, leaving current
vectors available until replacements commit. It does not delete vectors, chunks, extracted files,
assets, or summaries.

Implementation: [reembed_library.py](../../backend/scripts/reembed_library.py),
[embed_document](../../backend/app/workers/tasks.py), and the
[worker queue notes](../../backend/app/workers/README.md#celery-queue-split).

## Check the command and candidate count

Run from the backend directory, using the API container built from the same code as its worker:

```bash
docker compose exec api python scripts/reembed_library.py --help
docker compose exec api python scripts/reembed_library.py --dry-run
```

The help path exits before importing application settings or connecting to a database. The dry run
connects to the configured database and queries candidate document IDs, but queues no tasks. It
counts only documents whose embedding_mode is embedded and which have at least one chunk; it skips
fast-ingested papers marked skipped and documents with no chunks.

Confirm the selected Compose project and database before running the dry run. A dry run is a
database read, not a no-contact check.

## Queue the pass

After reviewing the candidate count and confirming the target environment, run:

```bash
docker compose exec api python scripts/reembed_library.py
```

The script queues one forced embed_document task for each eligible document and prints each task
ID. It does not wait for completion. The light worker consumes the default celery queue; its
environment and code must match the API's so task names and embedding settings agree.

Forced repair takes a stable snapshot of the document's chunk IDs and upserts replacement vectors
by chunk primary key. Existing vectors remain available until each replacement is committed. The
launcher does not invoke a model change, delete a vector table, or rewrite extraction artifacts.

## Verify completion

Follow worker logs for embed_document start and done records, which include the document ID and
embedded count. A queued task ID confirms publication only; it is not proof that a task completed.
If a task fails, inspect its worker error and retry only after resolving the cause. Source behavior
is in [tasks.py](../../backend/app/workers/tasks.py).

No re-embedding command was run while preparing this document. The procedure has not been exercised
against a live database or worker.
