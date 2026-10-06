"""Inspect, retry or resolve durable terminal failures. Run in the backend environment."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def parser():
    result=argparse.ArgumentParser(description=__doc__)
    commands=result.add_subparsers(dest='command',required=True)
    listing=commands.add_parser('list',help='List failures')
    listing.add_argument('--open',action='store_true')
    for command in ('show','retry','resolve'):
        commands.add_parser(command).add_argument('id',type=UUID)
    return result


async def retry(row_id):
    from sqlalchemy import text
    from app.database.connection import async_session_factory
    from app.services.ingestion import create_ingestion_job, requeue_failed_job
    from app.api.v1.endpoints.documents import _dispatch_upload
    async with async_session_factory() as db:
        # Locate its owner, then lock document before failure row, matching
        # terminal failure recording and the sweeper.
        row=(await db.execute(text('SELECT * FROM failed_jobs WHERE id=:id'),{'id':row_id})).mappings().first()
        if not row:
            raise ValueError('Failure not found')
        if row['status']!='open':
            raise ValueError('Only an open failure can be retried')
        doc=(await db.execute(text('SELECT * FROM documents WHERE id=:id FOR UPDATE'),{'id':row['document_id']})).mappings().first()
        if not doc:
            raise ValueError('The document no longer exists; resolve this failure instead')
        row=(await db.execute(text('SELECT * FROM failed_jobs WHERE id=:id FOR UPDATE'),{'id':row_id})).mappings().first()
        if not row or row['status']!='open' or row['document_id']!=doc['id']:
            raise ValueError('Only an open failure for this document can be retried')
        if row['task_name'] in ('9xaipal.process_ingestion','9xaipal.process_article_ingestion'):
            previous=(await db.execute(text('SELECT id,status FROM ingestion_jobs WHERE document_id=:doc ORDER BY created_at DESC,id DESC LIMIT 1'),{'doc':doc['id']})).mappings().first()
            job=await requeue_failed_job(db,previous['id']) if previous and previous['status']=='failed' else await create_ingestion_job(db,doc['id'])
            await db.execute(text("UPDATE documents SET status='processing',error_message=NULL,updated_at=now() WHERE id=:id"),{'id':doc['id']})
            await db.execute(text("UPDATE failed_jobs SET status='retried' WHERE id=:id"),{'id':row_id})
            await db.commit()
            # After adoption a URL document is now a PDF; use its normal path.
            await _dispatch_upload(db,dict(doc),job,url=doc['source_url'] if doc['doc_kind']=='article' else None)
        else:
            from app.core.celery_app import celery_app
            import app.workers.tasks
            task=celery_app.tasks.get(row['task_name'])
            if task is None or row['task_name'] not in {
                '9xaipal.embed_document','9xaipal.embed_document_search_vector',
                '9xaipal.generate_article_thumbnail','9xaipal.generate_section_summaries',
                '9xaipal.generate_figure_descriptions','9xaipal.reconstruct_reading_order'}:
                raise ValueError('This maintenance task has no document retry path; resolve it after fixing the cause')
            await db.execute(text("UPDATE failed_jobs SET status='retried' WHERE id=:id"),{'id':row_id})
            await db.commit()
            try:
                task.apply_async(args=[str(doc['id'])])
            except Exception:
                await db.execute(text("UPDATE failed_jobs SET status='open' WHERE id=:id"),{'id':row_id})
                await db.commit()
                raise


def main(argv=None):
    args=parser().parse_args(argv)
    if args.command=='retry':
        asyncio.run(retry(args.id))
        print('Retry queued')
        return
    from sqlalchemy import text
    from app.database.connection import sync_session
    with sync_session() as db:
        if args.command=='resolve':
            result=db.execute(text("UPDATE failed_jobs SET status='resolved' WHERE id=:id RETURNING id"),{'id':args.id}).first()
            if not result: raise ValueError('Failure not found')
            db.commit()
            print('Resolved')
        else:
            sql='SELECT * FROM failed_jobs'
            parameters={}
            if args.command=='show':
                sql+=' WHERE id=:id';parameters['id']=args.id
            elif args.open:
                sql+=" WHERE status='open'"
            rows=db.execute(text(sql+' ORDER BY last_failed_at DESC'),parameters).mappings().all()
            print(json.dumps([dict(row) for row in rows],default=str,indent=2))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        from app.services.failures import scrub
        print(scrub(exc,2048),file=sys.stderr)
        sys.exit(1)
