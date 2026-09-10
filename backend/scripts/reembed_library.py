"""Queue a safe full re-embedding pass for the current library.

This does not delete vectors or touch extracted files, chunks, assets, or
summaries. Each worker task regenerates a document's chunks and upserts by the
chunk primary key, so old vectors remain searchable until their replacements
are committed. Celery's configured worker concurrency controls how many whole
documents run at once; embedding batches remain bounded by
EMBEDDING_MAX_CONCURRENCY.

Run inside the backend container, for example:

    python scripts/reembed_library.py
"""

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.workers.tasks import embed_document


def main() -> None:
    engine = create_engine(settings.database_url_sync)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT d.id
                    FROM documents d
                    WHERE d.embedding_mode = 'embedded'
                      AND EXISTS (
                          SELECT 1 FROM chunks c WHERE c.document_id = d.id
                      )
                    ORDER BY d.created_at
                    """
                )
            ).mappings().all()
    finally:
        engine.dispose()

    if not rows:
        print("No embedded documents with chunks found.")
        return

    for row in rows:
        result = embed_document.delay(str(row["id"]), force=True)
        print(f"queued document={row['id']} task={result.id}")

    print(f"Queued safe full re-embedding for {len(rows)} document(s).")
