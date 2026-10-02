"""Round-four recovery regressions against local Postgres/Redis only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from celery.exceptions import Ignore, Reject
from sqlalchemy import text

from app.database.connection import async_session_factory, sync_session
from app.workers import tasks
from test_article_ingestion import _insert_document_and_job, _pdf_resource, _html_resource


async def test_confirmed_failed_execution_advances_generation(db_session_sync, tmp_path, monkeypatch):
    from app.extraction.arabic_types import ArabicStyleConfirmationRequired
    from app.extraction.pipeline_sync import _handle_ingestion_failure
    from app.api.v1.endpoints import documents as endpoint
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    user = uuid4()
    db_session_sync.execute(text("INSERT INTO users (id,email,password_hash) VALUES (:id,:email,'test-hash')"), {'id':user,'email':f'{user}@example.test'})
    db_session_sync.execute(text("UPDATE documents SET user_id=:user, filename='x.pdf' WHERE id=:id"), {'user':user,'id':doc})
    db_session_sync.commit()
    monkeypatch.setattr(endpoint.settings, 'arabic_ocr_enabled', True)
    monkeypatch.setenv('WORKER_ROLE', 'ingest')
    monkeypatch.setattr(tasks, 'documents_dir', lambda: tmp_path)
    monkeypatch.setattr(tasks, 'check_disk_headroom', lambda: None)
    (tmp_path / 'x.pdf').write_bytes(b'%PDF-')
    def failed(session, **kwargs):
        error = ArabicStyleConfirmationRequired()
        _handle_ingestion_failure(session, doc, job, error)
        raise error
    with patch.object(tasks, 'run_pipeline_sync', side_effect=failed):
        first = tasks.process_ingestion.apply(args=[str(doc), str(job), 'x.pdf'])
    assert first.state == 'FAILURE'
    import base64
    import json
    from kombu import Connection
    import redis
    broker, prefix = 'redis://host.docker.internal:55440/14', f'm4-confirm-{uuid4()}:'
    client = redis.Redis.from_url(broker)
    try:
        with Connection(broker, transport_options={'global_keyprefix':prefix}) as connection:
            def dispatch(*args, **kwargs):
                return tasks.process_ingestion.apply_async(args=args, kwargs=kwargs, connection=connection)
            with patch.object(tasks.process_ingestion, 'delay', side_effect=dispatch):
                async with async_session_factory() as session:
                    response = await endpoint.confirm_arabic_writing_style(doc, endpoint.ArabicWritingStyleConfirmation(writing_style='printed'), db=session, current_user={'id':user})
            assert response.status_code == 202
            envelope = json.loads(client.rpop(prefix+'ingest'))
            delivery_args, delivery_kwargs, _ = json.loads(base64.b64decode(envelope['body']))
            assert delivery_kwargs == {'execution_generation': 1}
    finally:
        keys = list(client.scan_iter(prefix+'*'))
        if keys: client.delete(*keys)
    calls = []
    with patch.object(tasks, 'run_pipeline_sync', side_effect=lambda *a, **k: calls.append('ran')):
        result = tasks.process_ingestion.apply(args=delivery_args, kwargs=delivery_kwargs, task_id=envelope['headers']['id'])
        assert result.state == 'SUCCESS'
        assert calls == ['ran']
        assert tasks.process_ingestion.apply(args=[str(doc), str(job), 'x.pdf'], task_id=first.id).state == 'IGNORED'
        assert calls == ['ran']
    row = db_session_sync.execute(text('SELECT execution_state, execution_error FROM ingestion_jobs WHERE id=:id'), {'id': job}).one()
    assert row == ('complete', None)
    assert db_session_sync.execute(text('SELECT detected_writing_style,classification_source FROM documents WHERE id=:id'), {'id':doc}).one() == ('printed','user_confirmed')


@pytest.mark.parametrize('late', ['failure', 'html', 'pdf'])
def test_article_lock_connection_loss_fences_stale_source(db_session_sync, tmp_path, monkeypatch, late):
    from app.extraction import pipeline_sync as ps
    from app.services.article_extraction import ArticleExtractionError
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    for name in ('documents_dir', 'assets_dir'):
        monkeypatch.setattr(ps, name, lambda: tmp_path)
    monkeypatch.setattr(ps, 'ensure_storage_dirs', lambda: None)
    first_fetch, release = Event(), Event()
    def fetch(url):
        if url.endswith('/first'):
            first_fetch.set()
            assert release.wait(10)
            if late == 'failure':
                raise ArticleExtractionError('stale source')
            return _html_resource() if late == 'html' else _pdf_resource()
        return _pdf_resource()
    def source(url):
        with sync_session() as session:
            return ps.run_article_pipeline_sync(session, document_id=doc, job_id=job, url=url)
    key = int.from_bytes(__import__('hashlib').sha256(f'article:{doc}'.encode()).digest()[:8], 'big', signed=True)
    with patch('app.services.article_extraction.fetch_resource', side_effect=fetch), patch('app.services.article_extraction.try_tavily_extract_fallback', return_value=None), patch.object(tasks.process_ingestion, 'apply_async'), patch.object(ps, '_finish_ingestion', side_effect=AssertionError('stale HTML persisted')):
        with ThreadPoolExecutor() as pool:
            first = pool.submit(source, 'https://example.test/first')
            assert first_fetch.wait(10)
            # Terminate only this test's article advisory-lock backend.
            pid = db_session_sync.execute(text("SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=:hi AND objid=:lo AND granted"), {'hi': (key >> 32) & 0xffffffff, 'lo': key & 0xffffffff}).scalar_one()
            db_session_sync.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': pid})
            db_session_sync.commit()
            try:
                assert source('https://example.test/second') is False
                with ExecutionClaim('ingestion', job, 'heavy'):
                    db_session_sync.execute(text("INSERT INTO chunks (document_id, sequence_id, markdown, plain_text) VALUES (:id,0,'retained','retained')"), {'id': doc})
                    db_session_sync.commit()
                    release.set()
                    with pytest.raises(Reject) as rejected:
                        first.result()
                    assert rejected.value.requeue
                    assert db_session_sync.execute(text('SELECT count(*) FROM chunks WHERE document_id=:id'), {'id': doc}).scalar_one() == 1
                    assert db_session_sync.execute(text('SELECT status FROM ingestion_jobs WHERE id=:id'), {'id': job}).scalar_one() != 'failed'
            finally:
                release.set()


def test_visibility_restore_live_owner_then_death_recovers_automatically(db_session_sync, tmp_path):
    import json
    import os
    import signal
    import subprocess
    import sys
    from celery import Celery
    from kombu import Connection
    import redis
    from test_heavy_task_canvas import wait_for
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    broker, prefix = 'redis://host.docker.internal:55440/14', f'm4-restore-{uuid4()}:'
    client = redis.Redis.from_url(broker)
    app = Celery('restore-client', broker=broker, backend=broker)
    app.conf.update(broker_transport_options={'global_keyprefix': prefix})
    (tmp_path / 'x.pdf').write_bytes(b'%PDF-')
    script = tmp_path / 'worker.py'
    script.write_text('''import os, sys, time
from pathlib import Path
import redis
from app.core.celery_app import celery_app as app, _sweep_extraction_scratch
from celery.signals import worker_init, task_postrun
from app.workers import tasks
from app.workers.execution_claims import ExecutionClaim
worker_init.disconnect(_sweep_extraction_scratch)
broker, prefix, directory = sys.argv[1:]
os.environ['WORKER_ROLE']='ingest'
ExecutionClaim.lease_seconds=2
ExecutionClaim.renew_interval=.2
ExecutionClaim.heartbeat_timeout=.8
app.conf.update(broker_url=broker, result_backend=broker, broker_transport_options={'global_keyprefix':prefix}, result_backend_transport_options={'global_keyprefix':prefix}, worker_lost_wait=.1)
client=redis.Redis.from_url(broker)
tasks.documents_dir=lambda: Path(directory)
tasks.check_disk_headroom=lambda: None
def pipeline(*a, **kw):
    client.rpush(prefix+'starts', os.getpid())
    if client.llen(prefix+'starts') == 1:
        while not client.exists(prefix+'release'): time.sleep(.03)
tasks.run_pipeline_sync=pipeline
@task_postrun.connect
def observed(state=None, **kw):
    if state in ('REJECTED','IGNORED'): client.rpush(prefix+'states',state)
app.worker_main(['worker','--pool=prefork','--concurrency=2','-Q','ingest','--without-gossip','--without-mingle','--without-heartbeat','--loglevel=WARNING'])
''')
    log = open(tmp_path / 'restore.log', 'w+')
    process = subprocess.Popen([sys.executable, str(script), broker, prefix, str(tmp_path)], stdout=log, stderr=log)
    try:
        app.send_task('9xaipal.process_ingestion', args=[str(doc), str(job), 'x.pdf'], queue='ingest')
        owner = int(wait_for(lambda: client.lindex(prefix+'starts', 0)))
        with Connection(broker, transport_options={'global_keyprefix': prefix}) as connection:
            tag = wait_for(lambda: next(iter(client.hkeys(prefix+'unacked')), None))
            connection.default_channel.qos.restore_by_tag(tag.decode())
        wait_for(lambda: client.llen(prefix+'states'))
        assert client.lindex(prefix+'states', 0) == b'REJECTED'
        assert client.llen(prefix+'starts') == 1
        os.kill(owner, signal.SIGKILL)  # only the owning child in this throwaway container
        wait_for(lambda: client.llen(prefix+'starts') == 2)
        wait_for(lambda: db_session_sync.execute(text('SELECT execution_state FROM ingestion_jobs WHERE id=:id'), {'id':job}).scalar_one() == 'complete')
        assert client.llen(prefix+'starts') == 2
    finally:
        client.set(prefix+'release', '1')
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.flush(); log.seek(0); print(log.read()[-3000:]); log.close()
        keys = list(client.scan_iter(prefix+'*'))
        if keys: client.delete(*keys)


def test_restart_runbook_distinguishes_restore_from_lease_admission():
    from pathlib import Path
    from app.workers.execution_claims import ExecutionClaim
    guide = (Path(__file__).resolve().parents[1] / 'DEPLOYMENT-PRODUCTION.md').read_text()
    section = guide.split('### What a restart does to a running ingestion', 1)[1].split('**The visibility timeout', 1)[0]
    assert 'starts over within seconds' not in section
    assert f'{ExecutionClaim.lease_seconds} seconds' in section
    assert 'extracting' in section and 'progress' in section
    assert 'restor' in section and 'admission' in section


def test_connection_loss_during_pdf_write_cannot_overwrite_adoption(db_session_sync, tmp_path, monkeypatch):
    from pathlib import Path
    from app.extraction import pipeline_sync as ps
    from app.services.article_extraction import FetchedResource
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    for name in ('documents_dir', 'assets_dir'):
        monkeypatch.setattr(ps, name, lambda: tmp_path)
    monkeypatch.setattr(ps, 'ensure_storage_dirs', lambda: None)
    paused, release = Event(), Event()
    original = Path.write_bytes
    def write(path, data):
        if data == b'%PDF-stale':
            paused.set()
            assert release.wait(10)
        return original(path, data)
    def source(url):
        with sync_session() as session:
            return ps.run_article_pipeline_sync(session, document_id=doc, job_id=job, url=url)
    def fetch(url):
        return FetchedResource(content=b'%PDF-stale' if url.endswith('first') else b'%PDF-adopted', content_type='application/pdf', final_url=url)
    key = int.from_bytes(__import__('hashlib').sha256(f'article:{doc}'.encode()).digest()[:8], 'big', signed=True)
    with patch('app.services.article_extraction.fetch_resource', side_effect=fetch), patch.object(tasks.process_ingestion, 'apply_async'), patch.object(Path, 'write_bytes', write):
        with ThreadPoolExecutor() as pool:
            stale = pool.submit(source, 'https://example.test/first')
            assert paused.wait(10)
            pid = db_session_sync.execute(text("SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=:hi AND objid=:lo AND granted"), {'hi':(key>>32)&0xffffffff,'lo':key&0xffffffff}).scalar_one()
            db_session_sync.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid':pid})
            db_session_sync.commit()
            try:
                assert source('https://example.test/second') is False
                path = tmp_path / f'{doc}.pdf'
                before = path.read_bytes(), path.stat().st_mtime_ns
                with ExecutionClaim('ingestion', job, 'heavy'):
                    release.set()
                    with pytest.raises(Reject): stale.result()
                    assert (path.read_bytes(),path.stat().st_mtime_ns) == before
                    assert path.read_bytes() == b'%PDF-adopted'
            finally:
                release.set()
