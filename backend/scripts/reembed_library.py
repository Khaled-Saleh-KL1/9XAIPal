"""Queue a safe full re-embedding pass for the current library.

This does not delete vectors or touch extracted files, chunks, assets, or
summaries. Each worker task regenerates a document's chunks and upserts by the
chunk primary key, so old vectors remain searchable until their replacements
are committed. Celery's configured worker concurrency controls how many whole
documents run at once; embedding batches remain bounded by
EMBEDDING_MAX_CONCURRENCY.

Run inside the backend container, for example:

    python scripts/reembed_library.py --dry-run
    python scripts/reembed_library.py
"""

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count the documents that would be re-embedded without queueing anything",
    )
    return parser.parse_args(argv)


def _eligible_document_ids() -> list[str]:
    # Imported here, not at module top: `--help` must not need the app,
    # its settings or a database connection.
    from sqlalchemy import create_engine, text

    from app.core.config import settings

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
    return [str(row["id"]) for row in rows]


def _embed_task():
    from app.workers.tasks import embed_document

    return embed_document


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    document_ids = _eligible_document_ids()

    if not document_ids:
        print("No embedded documents with chunks found.")
        return 0

    if args.dry_run:
        print(f"Would queue safe full re-embedding for {len(document_ids)} document(s).")
        return 0

    embed_document = _embed_task()
    for document_id in document_ids:
        result = embed_document.delay(document_id, force=True)
        print(f"queued document={document_id} task={result.id}")

    print(f"Queued safe full re-embedding for {len(document_ids)} document(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
