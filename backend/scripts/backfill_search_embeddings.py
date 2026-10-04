"""Queue missing library search vectors through the existing embedding task.

Run inside the backend container with:

    python scripts/backfill_search_embeddings.py

This command only selects eligible documents and enqueues work. Embedding is
performed later by the light Celery worker, under the shared bulk permit.
"""

from sqlalchemy import text

from app.database.connection import sync_session
from app.workers.tasks import embed_document_search_vector


def enqueue_missing_search_embeddings(
    session_factory=sync_session,
    task=embed_document_search_vector,
) -> int:
    """Enqueue documents that have chunks but no document search vector."""
    with session_factory() as session:
        rows = session.execute(
            text("""
                SELECT d.id
                FROM documents d
                WHERE d.search_embedding IS NULL
                  AND EXISTS (
                      SELECT 1 FROM chunks c WHERE c.document_id = d.id
                  )
                ORDER BY d.created_at, d.id
            """)
        ).mappings().all()

    for row in rows:
        task.delay(str(row["id"]))
    return len(rows)


def main() -> int:
    count = enqueue_missing_search_embeddings()
    print(f"Queued document search-vector work for {count} document(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
