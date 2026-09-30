"""Queue a safe re-embedding pass for Arabic-side documents only.

Run inside the backend container, optionally limiting the pass to one Arabic
document with ``python scripts/reembed_arabic.py --document-id UUID``.
"""

import argparse
from uuid import UUID

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.workers.tasks import embed_document


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", type=UUID, help="queue only this Arabic-side document")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    document_filter = "AND d.id = :document_id" if args.document_id else ""
    params = {"document_id": args.document_id} if args.document_id else {}
    engine = create_engine(settings.database_url_sync)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT d.id
                    FROM documents d
                    WHERE d.embedding_mode = 'embedded'
                      AND (
                          d.text_direction = 'rtl'
                          OR lower(coalesce(d.detected_language, '')) IN ('arabic', 'mixed')
                      )
                      AND EXISTS (
                          SELECT 1 FROM chunks c WHERE c.document_id = d.id
                      )
                      {document_filter}
                    ORDER BY d.created_at
                    """
                ),
                params,
            ).mappings().all()
    finally:
        engine.dispose()

    if not rows:
        print("No Arabic-side embedded documents with chunks found.")
        return

    for row in rows:
        result = embed_document.delay(str(row["id"]), force=True)
        print(f"queued Arabic document={row['id']} task={result.id}")

    print(f"Queued Arabic-side re-embedding for {len(rows)} document(s).")


if __name__ == "__main__":
    main()
