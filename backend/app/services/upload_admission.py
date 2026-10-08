"""Admission and content identity shared by PDF and article uploads."""
import json
import logging
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import text

from app.api.errors import UploadAdmissionError
from app.core.config import settings
from app.services.ingestion import check_queue_capacity, create_ingestion_job, requeue_failed_job
from app.services import documents as doc_service


logger = logging.getLogger(__name__)


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


async def reserve_upload(session, user_id, *, is_url=False, idem_key=None):
    """Reserve body storage and optionally a job using the job-creation lock.

    At capacity, at most one global request may verify an existing owner's
    identity. It may return duplicates but cannot create or retry a job.
    """
    from uuid import uuid4
    from app.api.errors import TooManyQueuedJobs
    from app.services.ingestion import check_disk_headroom
    await check_broker()
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('9xaipal:ingestion_queue'))"))
    await session.execute(text('DELETE FROM upload_reservations WHERE expires_at<=clock_timestamp()'))
    new_job = True
    try:
        await check_queue_capacity(session, user_id=user_id)
    except (TooManyQueuedJobs, UploadAdmissionError) as exc:
        if isinstance(exc, UploadAdmissionError) and exc.code not in ('queue_full','user_queue_full'):
            raise
        column = 'normalized_source_url' if is_url else 'content_sha256'
        known = await session.scalar(text(f"SELECT EXISTS(SELECT 1 FROM documents WHERE user_id=:user AND {column} IS NOT NULL AND status!='failed')"), {'user':user_id})
        if idem_key and not known:
            known = await session.scalar(text("""SELECT EXISTS(SELECT 1 FROM upload_idempotency
                WHERE user_id=:user AND key=:key AND created_at>now()-interval '24 hours')"""), {'user':user_id,'key':idem_key})
        busy = await session.scalar(text('SELECT EXISTS(SELECT 1 FROM upload_reservations WHERE NOT new_job)'))
        if not known or busy:
            raise
        new_job = False
    # Multipart spool, original and raw copy all count against the same 90%
    # rule. No separate free-GB threshold. Reserve conservatively at the cap.
    byte_budget = 65536 if is_url else 3 * settings.max_upload_size_mb * 1024**2 + 65536
    outstanding = await session.scalar(text('SELECT coalesce(sum(reserved_bytes),0) FROM upload_reservations'))
    check_disk_headroom(outstanding + byte_budget)
    token = uuid4()
    await session.execute(text("""INSERT INTO upload_reservations(id,user_id,new_job,reserved_bytes,expires_at)
        VALUES (:id,:user,:new,:bytes,clock_timestamp()+interval '10 minutes')"""),
        {'id':token,'user':user_id,'new':new_job,'bytes':byte_budget})
    await session.commit()
    return token


async def release_upload(token):
    from app.database.connection import async_session_factory
    try:
        async with async_session_factory() as session:
            await session.execute(text('DELETE FROM upload_reservations WHERE id=:id'), {'id':token})
            await session.commit()
    except Exception as exc:
        # Process/DB loss is covered by expiry; don't replace an API response.
        logger.warning('Upload reservation cleanup deferred: %s', type(exc).__name__)


async def renew_upload(token):
    from app.database.connection import async_session_factory
    async with async_session_factory() as session:
        updated = await session.scalar(text("""UPDATE upload_reservations
            SET expires_at=clock_timestamp()+interval '10 minutes'
            WHERE id=:id AND expires_at>clock_timestamp() RETURNING id"""), {'id':token})
        await session.commit()
        return updated is not None


async def verify_upload(token):
    if token is None:
        return
    try:
        valid = await renew_upload(token)
    except Exception:
        raise UploadAdmissionError('service_unavailable',503,120) from None
    if not valid:
        raise UploadAdmissionError('service_unavailable',503,120)


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
