"""Round-five article ownership/storage regressions on local test services."""
from unittest.mock import patch
from uuid import uuid4

import pytest
from celery.exceptions import Reject
from sqlalchemy import text

from _queue_test_helpers import redis_test_url
from app.workers import tasks
from test_article_ingestion import _insert_document_and_job, _pdf_resource


@pytest.mark.parametrize('residue', ['canonical', 'raw', 'both'])
def test_first_deploy_replaces_uncommitted_partial_pdf(db_session_sync, tmp_path, monkeypatch, residue):
    from app.extraction import pipeline_sync as ps
    import fitz
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    canonical, raw = tmp_path / 'documents', tmp_path / 'assets'
    canonical.mkdir(); raw.mkdir()
    monkeypatch.setattr(ps, 'documents_dir', lambda: canonical)
    monkeypatch.setattr(ps, 'assets_dir', lambda: raw)
    monkeypatch.setattr(ps, 'ensure_storage_dirs', lambda: None)
    with fitz.open() as pdf:
        pdf.new_page()
        complete = pdf.tobytes()
    filename = f'{doc}.pdf'
    if residue in ('canonical', 'both'):
        (canonical / filename).write_bytes(complete[:33])
    else:
        (canonical / filename).write_bytes(complete)
    if residue in ('raw', 'both'):
        (raw / filename).write_bytes(complete[:33])
    resource = _pdf_resource()
    resource.content = complete
    with patch('app.services.article_extraction.fetch_resource', return_value=resource), patch.object(tasks.process_ingestion, 'apply_async'):
        assert ps.run_article_pipeline_sync(db_session_sync, document_id=doc, job_id=job, url='https://example.test/paper.pdf') is False
    assert (canonical / filename).read_bytes() == complete
    assert (raw / filename).read_bytes() == complete
    assert db_session_sync.execute(text('SELECT file_size_bytes FROM documents WHERE id=:id'), {'id': doc}).scalar_one() == len(complete)
    with fitz.open(canonical / filename) as pdf:
        assert len(pdf) == 1



@pytest.mark.parametrize('boundary', ['cleanup', 'status'])
def test_article_failure_connection_loss_is_requeued(db_session_sync, monkeypatch, boundary):
    from app.extraction import pipeline_sync as ps
    from app.services.article_extraction import ArticleExtractionError
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    target = 'clean_slate_sync' if boundary == 'cleanup' else 'update_document_status_sync'
    original = getattr(ps, target)
    def lose_connection(session, *args, **kwargs):
        pid = session.connection().exec_driver_sql('SELECT pg_backend_pid()').scalar_one()
        db_session_sync.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': pid})
        db_session_sync.commit()
        return original(session, *args, **kwargs)
    with patch('app.services.article_extraction.fetch_resource', side_effect=ArticleExtractionError('fetch failed')), patch('app.services.article_extraction.try_tavily_extract_fallback', return_value=None), patch.object(ps, target, side_effect=lose_connection):
        result = tasks.process_article_ingestion.apply(args=[str(doc), str(job), 'https://example.test/failure'])
    assert result.state == 'REJECTED'
    assert db_session_sync.execute(text('SELECT status FROM ingestion_jobs WHERE id=:id'), {'id': job}).scalar_one() == 'extracting'



@pytest.mark.parametrize('boundary', ['cleanup', 'status'])
def test_article_worker_recovers_connection_loss_during_failure(db_session_sync, tmp_path, boundary):
    import os
    import subprocess
    import sys
    import json
    from celery import Celery
    import redis
    from test_heavy_task_canvas import wait_for
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    broker, prefix = redis_test_url(), f'm5-cleanup-{uuid4()}:'
    client = redis.Redis.from_url(broker)
    app = Celery('cleanup-client', broker=broker, backend=broker)
    app.conf.update(broker_transport_options={'global_keyprefix': prefix}, result_backend_transport_options={'global_keyprefix': prefix})
    script = tmp_path / 'worker.py'
    script.write_text('''import os,sys
import redis
from sqlalchemy import text
from celery.signals import worker_init,task_postrun
from app.core.celery_app import celery_app as app,_sweep_extraction_scratch
from app.database.connection import sync_engine
from app.workers import tasks
from app.extraction import pipeline_sync as ps
from app.services import article_extraction as ae
worker_init.disconnect(_sweep_extraction_scratch)
broker,prefix,boundary=sys.argv[1:]
os.environ['WORKER_ROLE']='light'
client=redis.Redis.from_url(broker)
app.conf.update(broker_url=broker,result_backend=broker,broker_transport_options={'global_keyprefix':prefix},result_backend_transport_options={'global_keyprefix':prefix})
def fetch(url):
    client.incr(prefix+'fetches')
    raise ae.ArticleExtractionError('fetch failed')
ae.fetch_resource=fetch
ae.try_tavily_extract_fallback=lambda *a:None
name='clean_slate_sync' if boundary=='cleanup' else 'update_document_status_sync'
original=getattr(ps,name)
def boundary_loss(session,*a,**kw):
    if client.set(prefix+'lost','1',nx=True):
        pid=session.connection().exec_driver_sql('SELECT pg_backend_pid()').scalar_one()
        with sync_engine.begin() as connection: connection.execute(text('SELECT pg_terminate_backend(:pid)'),{'pid':pid})
    return original(session,*a,**kw)
setattr(ps,name,boundary_loss)
@task_postrun.connect
def observed(state=None,**kw):
    if state=='REJECTED': client.rpush(prefix+'states',state)
@app.task(name='m5.article_errback')
def errback(request,exc,tb): client.rpush(prefix+'errors',str(exc))
app.worker_main(['worker','--pool=solo','-Q','celery','--without-gossip','--without-mingle','--without-heartbeat','--loglevel=WARNING'])
''')
    log = open(tmp_path / 'worker.log', 'w+')
    worker = subprocess.Popen([sys.executable, str(script), broker, prefix, boundary], stdout=log, stderr=log)
    try:
        result = app.send_task('9xaipal.process_article_ingestion', args=[str(doc), str(job), 'https://example.test/failure'], queue='celery', link_error=app.signature('m5.article_errback'))
        wait_for(lambda: client.llen(prefix+'states'))
        assert client.lindex(prefix+'states', 0) == b'REJECTED'
        wait_for(lambda: result.ready())
        assert result.failed()
        assert wait_for(lambda: client.lindex(prefix+'errors', 0)) == b'fetch failed'
        assert int(client.get(prefix+'fetches')) == 2
        assert db_session_sync.execute(text('SELECT status FROM ingestion_jobs WHERE id=:id'), {'id': job}).scalar_one() == 'failed'
        assert db_session_sync.execute(text('SELECT status FROM documents WHERE id=:id'), {'id': doc}).scalar_one() == 'failed'
        assert client.llen(prefix+'celery') == 0
    finally:
        print('article broker state', {name: client.llen(prefix+name) for name in ('celery','states','errors')}, client.hgetall(prefix+'unacked'), list(client.scan_iter(prefix+'*')))
        worker.terminate()
        try: worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill(); worker.wait(timeout=5)
        log.flush(); log.seek(0); print(log.read()[-3000:]); log.close()
        keys = list(client.scan_iter(prefix+'*'))
        if keys: client.delete(*keys)



def test_filesystem_fence_survives_db_loss_during_raw_publication(db_session_sync, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from pathlib import Path
    from app.database.connection import sync_session
    from app.extraction import pipeline_sync as ps
    from app.services.article_extraction import FetchedResource
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    canonical, raw = tmp_path / 'canonical', tmp_path / 'raw'
    canonical.mkdir(); raw.mkdir()
    monkeypatch.setattr(ps, 'documents_dir', lambda: canonical)
    monkeypatch.setattr(ps, 'assets_dir', lambda: raw)
    monkeypatch.setattr(ps, 'ensure_storage_dirs', lambda: None)
    paused, released, second_staged = Event(), Event(), Event()
    original = Path.write_bytes
    def write(path, data):
        if path.parent == raw and data == b'%PDF-stale':
            paused.set()
            assert released.wait(10)
        if path.parent == canonical and data == b'%PDF-new':
            second_staged.set()
        return original(path, data)
    def source(url):
        with sync_session() as session:
            return ps.run_article_pipeline_sync(session, document_id=doc, job_id=job, url=url)
    def fetch(url):
        return FetchedResource(content=b'%PDF-stale' if url.endswith('first') else b'%PDF-new', content_type='application/pdf', final_url=url)
    import hashlib
    key = int.from_bytes(hashlib.sha256(f'article:{doc}'.encode()).digest()[:8], 'big', signed=True)
    with patch('app.services.article_extraction.fetch_resource', side_effect=fetch), patch.object(tasks.process_ingestion, 'apply_async'), patch.object(Path, 'write_bytes', write):
        with ThreadPoolExecutor() as pool:
            first = pool.submit(source, 'https://example.test/first')
            assert paused.wait(10)
            pid = db_session_sync.execute(text("SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=:hi AND objid=:lo AND granted"), {'hi': (key >> 32) & 0xffffffff, 'lo': key & 0xffffffff}).scalar_one()
            db_session_sync.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': pid})
            db_session_sync.commit()
            try:
                second = pool.submit(source, 'https://example.test/second')
                assert second_staged.wait(10)
                assert not second.done(), 'adoption must wait for a stale file publisher to stop'
                released.set()
                with pytest.raises(Reject): first.result()
                assert second.result() is False
            finally:
                released.set()
    assert (canonical / f'{doc}.pdf').read_bytes() == b'%PDF-new'
    assert (raw / f'{doc}.pdf').read_bytes() == b'%PDF-new'
