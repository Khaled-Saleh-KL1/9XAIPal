"""Queue generated thumbnails for complete articles without a cached cover.

Usage (inside the backend container):
    python scripts/backfill_article_thumbnails.py --dry-run
    python scripts/backfill_article_thumbnails.py --limit 100 --user <uuid>
"""

import argparse
import sys
from pathlib import Path
from uuid import UUID

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.database.connection import sync_session
from app.services.covers import cover_path
from app.workers.tasks import generate_article_thumbnail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count eligible articles without enqueuing thumbnail work",
    )
    parser.add_argument("--limit", type=int, help="queue at most N eligible articles")
    parser.add_argument("--user", type=UUID, help="only queue articles owned by this user")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    with sync_session() as session:
        rows = session.execute(
            text("""
                SELECT id
                FROM documents
                WHERE doc_kind = 'article'
                  AND status = 'complete'
                  AND (:user_id IS NULL OR user_id = :user_id)
                ORDER BY created_at, id
            """),
            {"user_id": args.user},
        ).mappings().all()

    eligible = []
    for row in rows:
        path = cover_path(row["id"])
        try:
            if path.is_file() and path.stat().st_size > 0:
                continue
        except OSError:
            # An inaccessible cache entry is not treated as a valid cover.
            pass
        eligible.append(row["id"])
        if args.limit is not None and len(eligible) >= args.limit:
            break

    if not args.dry_run:
        for document_id in eligible:
            generate_article_thumbnail.delay(str(document_id))

    action = "Would queue" if args.dry_run else "Queued"
    print(f"{action} article thumbnail work for {len(eligible)} document(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
