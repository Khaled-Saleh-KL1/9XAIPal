"""Admission and content identity shared by PDF and article uploads."""
import json
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import text

from app.api.errors import UploadAdmissionError, InsufficientStorage
from app.core.config import settings
from app.services.ingestion import check_queue_capacity, create_ingestion_job, requeue_failed_job
from app.services import documents as doc_service


def retry_after(depth: int) -> int:
    return max(30, min(600, 30 * max(1, depth)))


async def check_broker():
    import redis.asyncio as redis
    # Authentication Redis and broker can be configured independently.
    for url in {settings.redis_url, settings.effective_celery_broker_url}:
        if url.startswith(('redis://', 'rediss://')):
            client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
            try:
                await client.ping()
            except Exception:
                raise UploadAdmissionError('service_unavailable', 503, 120) from None
            finally:
                await client.aclose()
        else:
            from app.core.celery_app import celery_app
            from starlette.concurrency import run_in_threadpool
            def probe():
                with celery_app.connection_for_write() as conn:
                    conn.ensure_connection(max_retries=0, timeout=2)
            try:
                await run_in_threadpool(probe)
            except Exception:
                raise UploadAdmissionError('service_unavailable', 503, 120) from None


async def check_upload_admission(session, user_id):
    await check_broker()
    try:
        await check_queue_capacity(session, user_id=user_id)
    except InsufficientStorage:
        raise UploadAdmissionError('storage_full', 503, 600) from None


def normalize_url(url):
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, 'Use a public http or https URL without credentials.')
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(422, 'The URL has an invalid port.') from None
    host = parsed.hostname.lower()
    if ':' in host:
        host = f'[{host}]'
    if port and not (parsed.scheme.lower() == 'https' and port == 443 or parsed.scheme.lower() == 'http' and port == 80):
        host += f':{port}'
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or '/', parsed.query, ''))


async def accept_identity(session, *, user_id, identity, is_url=False, idem_key=None, **fields):
    """Transaction-scoped identity lock plus a database unique index.

    All normal job creation still uses the existing global reservation. The
    optional key has its own lock so different hashes cannot win the same key.
    """
    if idem_key:
        if len(idem_key) > 200:
            raise HTTPException(422, 'Idempotency-Key is too long.')
        await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key':f'idem:{user_id}:{idem_key}'})
        replay = (await session.execute(text('SELECT identity,document_id FROM upload_idempotency WHERE user_id=:user AND key=:key AND created_at>now()-interval \'24 hours\''), {'user':user_id,'key':idem_key})).mappings().first()
        if replay:
            if replay['identity'] != identity:
                raise HTTPException(422, 'This upload key was already used for a different file or URL.')
            doc = (await session.execute(text('SELECT * FROM documents WHERE id=:id'), {'id':replay['document_id']})).mappings().first()
            if doc and doc['status'] != 'failed':
                return dict(doc), None, True
    await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key':f'upload:{user_id}:{identity}'})
    column = 'normalized_source_url' if is_url else 'content_sha256'
    doc = (await session.execute(text(f'SELECT * FROM documents WHERE user_id=:user AND {column}=:identity FOR UPDATE'), {'user':user_id,'identity':identity})).mappings().first()
    duplicate = bool(doc)
    job = None
    if doc:
        doc = dict(doc)
        if doc['status'] == 'failed':
            # Reuse a failed job with a fresh fenced execution generation.
            previous = (await session.execute(text('SELECT id,status FROM ingestion_jobs WHERE document_id=:doc ORDER BY created_at DESC,id DESC LIMIT 1'), {'doc':doc['id']})).mappings().first()
            job = await requeue_failed_job(session,previous['id']) if previous and previous['status']=='failed' else await create_ingestion_job(session,doc['id'])
            await session.execute(text("UPDATE documents SET status='processing',error_message=NULL,updated_at=now() WHERE id=:id"), {'id':doc['id']})
            await session.execute(text("UPDATE failed_jobs SET status='retried' WHERE job_id=:job AND status='open'"), {'job':job['id']})
            doc['status'] = 'processing'
    else:
        doc = await doc_service.create_document(session, user_id=user_id, **fields)
        await session.execute(text(f'UPDATE documents SET {column}=:identity WHERE id=:id'), {'id':doc['id'],'identity':identity})
        job = await create_ingestion_job(session, doc['id'])
    if idem_key:
        await session.execute(text('''INSERT INTO upload_idempotency(user_id,key,identity,document_id)
            VALUES (:user,:key,:identity,:doc) ON CONFLICT (user_id,key) DO UPDATE
            SET identity=EXCLUDED.identity,document_id=EXCLUDED.document_id,created_at=now()'''), {'user':user_id,'key':idem_key,'identity':identity,'doc':doc['id']})
    return doc, job, duplicate


async def cache_idempotency(user_id, key, identity, document_id):
    if not key:
        return
    # DB ledger is authoritative during Redis loss or publication failure.
    try:
        from app.core.redis import get_redis
        await get_redis().set(f'idem:{user_id}:{key}', json.dumps({'document_id':str(document_id),'hash':identity}), nx=True, ex=86400)
    except Exception:
        pass
