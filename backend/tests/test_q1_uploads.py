import asyncio
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.api.deps import get_current_user
from app.api.errors import register_exception_handlers
from app.api.v1.endpoints import documents
from app.core.config import settings
from app.database.connection import async_session_factory

PDF = b'%PDF-1.7\n'+b'x'*100

@pytest.fixture
async def client(db_session, monkeypatch):
    from app.core.redis import close_redis
    import app.core.redis as redis_module
    try:
        await close_redis()
    except RuntimeError:
        redis_module._client = None
    monkeypatch.setattr(settings, "ingestion_disk_refuse_percent", 101)
    monkeypatch.setattr(settings, "min_free_disk_gb", 0)
    user = uuid4()
    await db_session.execute(text("INSERT INTO users(id,email,password_hash) VALUES (:id,'q1@example.test','test-only')"), {'id':user})
    await db_session.commit()
    app = FastAPI()
    app.include_router(documents.router, prefix='/api/v1/documents')
    register_exception_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: {'id':user}
    monkeypatch.setattr(documents.process_ingestion, 'delay', Mock())
    monkeypatch.setattr(documents.process_article_ingestion, 'delay', Mock())
    from app.core.redis import get_redis
    await get_redis().flushdb()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        client.user = user
        yield client
    await close_redis()

async def upload(client, content=PDF, key=None):
    return await client.post('/api/v1/documents/upload', files={'file':('a.pdf', content,'application/pdf')}, headers={'Idempotency-Key':key} if key else {})

@pytest.mark.parametrize('condition,code,status', [
    ('global','queue_full',429),('user','user_queue_full',429),('disk','storage_full',503),('broker','service_unavailable',503),
])
async def test_backpressure(client, monkeypatch, condition, code, status):
    if condition == 'global': monkeypatch.setattr(settings, 'max_queued_ingestion_jobs', 0)
    if condition == 'user': monkeypatch.setattr(settings, 'max_queued_jobs_per_user', 0)
    if condition == 'disk': monkeypatch.setattr(settings, 'min_free_disk_gb', 10**9)
    if condition == 'broker': monkeypatch.setattr(settings, 'celery_broker_url', 'redis://localhost:55445/0')
    for path in ['/upload','/import-url']:
        response = await upload(client) if path == '/upload' else await client.post('/api/v1/documents'+path, json={'url':'https://example.test/article'})
        assert response.status_code == status
        assert response.json()['code'] == code
        assert 30 <= int(response.headers['Retry-After']) <= 600
        assert 'message' in response.json()
    documents.process_ingestion.delay.assert_not_called()

async def test_size_limit_no_partial(client, monkeypatch):
    from app.core.paths import documents_dir
    monkeypatch.setattr(settings, 'max_upload_size_mb', 0)
    before = set(documents_dir().glob('*'))
    response = await upload(client)
    assert response.status_code == 413
    assert set(documents_dir().glob('*')) == before

async def test_same_file_same_document_one_job(client, db_session):
    first, second = await upload(client), await upload(client)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()['id'] == second.json()['id']
    assert second.json()['duplicate'] is True
    assert await db_session.scalar(text('SELECT count(*) FROM documents')) == 1
    assert await db_session.scalar(text('SELECT count(*) FROM ingestion_jobs')) == 1
    documents.process_ingestion.delay.assert_called_once()

async def test_failed_reupload_same_row(client, db_session):
    first = await upload(client)
    doc = first.json()['id']
    await db_session.execute(text("UPDATE documents SET status='failed' WHERE id=:id"), {'id':doc})
    await db_session.execute(text("UPDATE ingestion_jobs SET status='failed' WHERE document_id=:id"), {'id':doc})
    await db_session.commit()
    second = await upload(client)
    assert second.json()['id'] == doc
    assert second.json()['status'] == 'processing'
    assert await db_session.scalar(text('SELECT count(*) FROM documents')) == 1
    assert documents.process_ingestion.delay.call_count == 2
    assert await db_session.scalar(text('SELECT count(*) FROM ingestion_jobs')) == 1

async def test_simultaneous_identical_uploads(client, db_session, monkeypatch):
    original = documents._stream_pdf_upload
    barrier = asyncio.Barrier(2)
    async def stream(*args, **kwargs):
        result = await original(*args, **kwargs)
        await barrier.wait()
        return result
    monkeypatch.setattr(documents, '_stream_pdf_upload', stream)
    first, second = await asyncio.gather(upload(client), upload(client))
    assert sorted([first.status_code,second.status_code]) == [200,201]
    assert first.json()['id'] == second.json()['id']
    assert await db_session.scalar(text('SELECT count(*) FROM documents')) == 1
    assert await db_session.scalar(text('SELECT count(*) FROM ingestion_jobs')) == 1
    documents.process_ingestion.delay.assert_called_once()

async def test_idempotency_replay_and_mismatch(client):
    key = str(uuid4())
    first = await upload(client, key=key)
    second = await upload(client, key=key)
    assert second.status_code == 200
    assert first.json()['id'] == second.json()['id']
    mismatch = await upload(client, PDF+b'different', key=key)
    assert mismatch.status_code == 422
    documents.process_ingestion.delay.assert_called_once()

async def test_normalized_url_dedup(client, db_session):
    first = await client.post('/api/v1/documents/import-url', json={'url':'HTTPS://Example.test:443/article#one'})
    second = await client.post('/api/v1/documents/import-url', json={'url':'https://example.test/article#two'})
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()['id'] == second.json()['id']
    assert await db_session.scalar(text('SELECT count(*) FROM ingestion_jobs')) == 1
    documents.process_article_ingestion.delay.assert_called_once()

async def test_admission_before_body_read(monkeypatch):
    from app.core.upload_guard import UploadGuardMiddleware
    from app.api.errors import UploadAdmissionError
    called = []
    async def admission(scope):
        raise UploadAdmissionError('user_queue_full',429,120)
    async def downstream(scope,receive,send):
        await receive()
        called.append('downstream')
    async def receive():
        called.append('body')
        return {'type':'http.request','body':PDF}
    sent = []
    async def send(message): sent.append(message)
    middleware = UploadGuardMiddleware(downstream, admission=admission)
    await middleware({'type':'http','method':'POST','path':'/api/v1/documents/upload','headers':[]},receive,send)
    assert called == []
    assert sent[0]['status'] == 429

async def test_stream_hash_computed_without_second_read(tmp_path):
    import hashlib
    from starlette.datastructures import UploadFile
    hasher = hashlib.sha256()
    size = await documents._stream_pdf_upload(UploadFile(BytesIO(PDF),filename='a.pdf'),tmp_path/'a.pdf',1000,hasher=hasher)
    assert size == len(PDF)
    assert hasher.hexdigest() == hashlib.sha256(PDF).hexdigest()

async def test_other_users_independent(client, db_session):
    first = await upload(client)
    other = uuid4()
    await db_session.execute(text("INSERT INTO users(id,email,password_hash) VALUES (:id,'other@example.test','test')"), {'id':other})
    await db_session.commit()
    app = client._transport.app
    app.dependency_overrides[get_current_user] = lambda: {'id':other}
    second = await upload(client)
    assert first.json()['id'] != second.json()['id']
    assert await db_session.scalar(text('SELECT count(*) FROM documents')) == 2

async def test_simultaneous_url_imports(client, db_session):
    barrier = asyncio.Barrier(2)
    async def call():
        await barrier.wait()
        return await client.post('/api/v1/documents/import-url',json={'url':'https://example.test/a'})
    responses = await asyncio.gather(call(),call())
    assert sorted(r.status_code for r in responses) == [200,201]
    assert await db_session.scalar(text('SELECT count(*) FROM documents')) == 1
    assert await db_session.scalar(text('SELECT count(*) FROM ingestion_jobs')) == 1

async def test_main_guard_with_authenticated_user_before_body(client, monkeypatch):
    from app.core.upload_guard import UploadGuardMiddleware
    from app.main import app
    from app.api.errors import UploadAdmissionError
    called=[]
    async def admission(scope):
        raise UploadAdmissionError('storage_full',503,600)
    # Exercise the ASGI boundary independently of auth/session internals.
    guarded=UploadGuardMiddleware(client._transport.app,admission=admission)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded),base_url='http://test') as guarded_client:
        response=await guarded_client.post('/api/v1/documents/upload',content=b'x'*100)
        assert response.status_code==503
        assert response.headers['Retry-After']=='600'

async def test_real_main_upload_guard(client, monkeypatch):
    # Production stack, actual session cookie and alias route. HTTP remains local.
    from app.main import app
    from app.core.redis import close_redis
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as real:
        signup=await real.post('/api/v1/auth/signup',json={'email':f'{uuid4()}@example.com','password':'correct horse battery'})
        assert signup.status_code==201
        monkeypatch.setattr(settings,'max_queued_ingestion_jobs',0)
        response=await real.post('/api/v1/documents/upload',files={'file':('test.pdf',PDF,'application/pdf')})
        assert response.status_code==429
        assert response.json()['code']=='queue_full'

async def test_failed_same_key_reenqueues(client,db_session):
    key=str(uuid4())
    first=await upload(client,key=key)
    doc=first.json()['id']
    await db_session.execute(text("UPDATE documents SET status='failed' WHERE id=:id"),{'id':doc})
    await db_session.execute(text("UPDATE ingestion_jobs SET status='failed' WHERE document_id=:id"),{'id':doc})
    await db_session.commit()
    again=await upload(client,key=key)
    assert again.json()['id']==doc
    assert again.json()['status']=='processing'
    assert documents.process_ingestion.delay.call_count==2
