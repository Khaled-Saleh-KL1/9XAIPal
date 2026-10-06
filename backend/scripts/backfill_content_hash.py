"""Opt-in streaming hash backfill. Existing duplicates are reported, never deleted."""
import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    from sqlalchemy import text
    from app.database.connection import sync_session
    from app.core.paths import documents_dir
    from app.services.failures import scrub
    with sync_session() as db:
        rows=db.execute(text("SELECT id,user_id,filename FROM documents WHERE content_sha256 IS NULL AND doc_kind!='article' ORDER BY created_at,id")).mappings().all()
        db.commit()
        seen=set()
        for row in rows:
            path=documents_dir()/row['filename']
            if not path.is_file():
                print(f"{row['id']}: original missing; skipped")
                continue
            hasher=hashlib.sha256()
            with path.open('rb') as source:
                while data:=source.read(1024*1024): hasher.update(data)
            sha=hasher.hexdigest()
            key=(row['user_id'],sha)
            existing=db.execute(text('SELECT id FROM documents WHERE user_id=:user AND content_sha256=:sha'),{'user':row['user_id'],'sha':sha}).scalar()
            if key in seen or existing:
                print(f"{row['id']}: duplicate original; left unhashed for operator review")
                continue
            seen.add(key)
            if args.apply:
                try:
                    # Share the request lock so a concurrent upload cannot
                    # race the backfill's check and hash publication.
                    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':f"upload:{row['user_id']}:{sha}"})
                    db.execute(text('UPDATE documents SET content_sha256=:sha WHERE id=:id AND content_sha256 IS NULL'),{'sha':sha,'id':row['id']})
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    print(f"{row['id']}: skipped: {scrub(exc,300)}")
                    continue
            print(f"{row['id']}: {'hashed' if args.apply else 'would hash'} {sha}")


if __name__=='__main__':
    main()
