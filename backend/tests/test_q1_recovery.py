from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.services import failures
from test_article_ingestion import _insert_document_and_job


def test_alert_delivery_concurrent_real_redis(db_session_sync,monkeypatch):
    monkeypatch.setattr(failures,'queue_alert',Mock())
    monkeypatch.setattr(settings,'smtp_host','smtp.example.test')
    monkeypatch.setattr(settings,'alert_email_to','owner@example.test')
    monkeypatch.setattr(settings,'alert_email_from','alert@example.test')
    smtp=MagicMock()
    monkeypatch.setattr('smtplib.SMTP',smtp)
    failures.alert_client().flushdb()
    rows=[failures.record_failure('9xaipal.concurrent',str(uuid4()),RuntimeError('provider down')) for _ in range(2)]
    barrier=Barrier(2)
    def send(row):
        barrier.wait()
        failures.send_row_alert(row['id'])
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(send,rows))
    sender=smtp.return_value.__enter__.return_value
    assert sender.send_message.call_count==1
    message=sender.send_message.call_args.args[0]
    assert message['Subject']=='[9XAIPal] system: RuntimeError in 9xaipal.concurrent'
    assert 'python backend/scripts/dlq.py retry ' in message.get_content()


def test_old_article_generation_does_not_fetch(db_session_sync,monkeypatch):
    from app.workers import tasks
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    db_session_sync.execute(text('UPDATE ingestion_jobs SET execution_generation=1 WHERE id=:id'),{'id':job})
    db_session_sync.commit()
    fetch=Mock(side_effect=AssertionError('stale task fetched'))
    monkeypatch.setattr('app.services.article_extraction.fetch_resource',fetch)
    monkeypatch.setattr(tasks,'_queue_article_thumbnail',Mock())
    tasks.process_article_ingestion.run(str(doc),str(job),'https://example.test/a',execution_generation=0)
    fetch.assert_not_called()


def test_arabic_terminal_failure_preserves_confirmation_code(db_session_sync,monkeypatch):
    from app.extraction.arabic_types import ArabicStyleConfirmationRequired
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    monkeypatch.setattr(failures,'queue_alert',Mock())
    error=ArabicStyleConfirmationRequired()
    failures.record_failure('9xaipal.process_ingestion','delivery',error,document_id=doc,job_id=job)
    code=db_session_sync.scalar(text('SELECT error_code FROM ingestion_jobs WHERE id=:id'),{'id':job})
    assert code==error.error_code
    message=db_session_sync.scalar(text('SELECT error_message FROM documents WHERE id=:id'),{'id':doc})
    assert message==error.public_message


@pytest.mark.parametrize('error_class', ['ArabicStyleConfirmationRequired', 'HandwrittenArabicUnavailable', 'ArabicClassifierUnavailableError'])
def test_celery_base_exception_preserves_persisted_typed_failure(db_session_sync,monkeypatch,error_class):
    from app.extraction import arabic_types
    error=getattr(arabic_types,error_class)()
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET status='failed',error_code=:code,error_message=:message WHERE id=:id"),{'id':job,'code':error.error_code,'message':error.public_message})
    db_session_sync.commit()
    monkeypatch.setattr(failures,'queue_alert',Mock())
    # Celery's get_pickleable_exception falls back to the base class for
    # these zero-argument constructors. Retain the pipeline's typed state.
    row=failures.record_failure('9xaipal.process_ingestion','delivery',arabic_types.ArabicRoutingError(error.public_message),document_id=doc,job_id=job)
    assert db_session_sync.scalar(text('SELECT error_code FROM ingestion_jobs WHERE id=:id'),{'id':job})==error.error_code
    assert db_session_sync.scalar(text('SELECT error_message FROM documents WHERE id=:id'),{'id':doc})==error.public_message
    assert row['error_type']==error_class
    assert row['category']==('system' if error_class=='ArabicClassifierUnavailableError' else 'user_input')


def test_deleted_document_task_still_records_failure(db_session_sync,monkeypatch):
    monkeypatch.setattr(failures,'queue_alert',Mock())
    row=failures.record_failure('9xaipal.process_ingestion','gone',RuntimeError('worker lost'),document_id=uuid4(),job_id=uuid4())
    assert row['document_id'] is None
    assert row['job_id'] is None


def test_smtp_task_failure_does_not_enqueue_recursive_alert(db_session_sync,monkeypatch):
    from app.workers import reliability
    queued=Mock()
    monkeypatch.setattr(failures,'queue_alert',queued)
    monkeypatch.setattr(failures,'send_row_alert',Mock(side_effect=OSError('SMTP unavailable')))
    result=reliability.send_failure_alert.apply(args=[str(uuid4())],throw=False)
    assert result.failed()
    assert db_session_sync.scalar(text("SELECT count(*) FROM failed_jobs WHERE task_name='9xaipal.send_failure_alert'"))==1
    queued.assert_not_called()


def test_summary_terminal_failure_marks_inprogress_document(db_session_sync,monkeypatch):
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    monkeypatch.setattr(failures,'queue_alert',Mock())
    failures.record_failure('9xaipal.generate_section_summaries','summary-failed',RuntimeError('DB down'),document_id=doc)
    assert db_session_sync.scalar(text('SELECT status FROM documents WHERE id=:id'),{'id':doc})=='failed'


async def test_dlq_retry_reuses_job_and_resolve(db_session,monkeypatch):
    import importlib.util
    from pathlib import Path
    from app.workers import tasks
    from app.api.v1.endpoints import documents
    from app.database.connection import sync_session_factory
    from app.services.ingestion import create_ingestion_job
    monkeypatch.setattr(settings,'ingestion_disk_refuse_percent',101)
    monkeypatch.setattr(settings,'min_free_disk_gb',0)
    owner,doc=uuid4(),uuid4()
    await db_session.execute(text("INSERT INTO users(id,email,password_hash) VALUES (:id,'retry@example.test','test')"),{'id':owner})
    await db_session.execute(text("INSERT INTO documents(id,user_id,filename,original_filename,status) VALUES (:id,:owner,'a.pdf','a.pdf','processing')"),{'id':doc,'owner':owner})
    job=await create_ingestion_job(db_session,doc)
    await db_session.commit()
    monkeypatch.setattr(failures,'queue_alert',Mock())
    row=failures.record_failure('9xaipal.process_ingestion','failed',RuntimeError('provider down'),document_id=doc,job_id=job['id'])
    monkeypatch.setattr(documents.process_ingestion,'delay',Mock())
    spec=importlib.util.spec_from_file_location('dlq_script',Path('scripts/dlq.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    await module.retry(row['id'])
    assert await db_session.scalar(text('SELECT count(*) FROM ingestion_jobs'))==1
    assert await db_session.scalar(text('SELECT execution_generation FROM ingestion_jobs WHERE id=:id'),{'id':job['id']})==1
    assert await db_session.scalar(text('SELECT status FROM failed_jobs WHERE id=:id'),{'id':row['id']})=='retried'
    module.main(['resolve',str(row['id'])])
    assert await db_session.scalar(text('SELECT status FROM failed_jobs WHERE id=:id'),{'id':row['id']})=='resolved'


@pytest.mark.parametrize('task_name',['9xaipal.process_ingestion','9xaipal.embed_document'])
async def test_dlq_fast_refailure_stays_open(db_session,monkeypatch,task_name):
    import importlib.util
    from pathlib import Path
    from app.workers import tasks
    from app.services.ingestion import create_ingestion_job
    monkeypatch.setattr(settings,'ingestion_disk_refuse_percent',101)
    monkeypatch.setattr(settings,'min_free_disk_gb',0)
    owner,doc=uuid4(),uuid4()
    await db_session.execute(text("INSERT INTO users(id,email,password_hash) VALUES (:id,'fast-retry@example.test','test')"),{'id':owner})
    await db_session.execute(text("INSERT INTO documents(id,user_id,filename,original_filename,status) VALUES (:id,:owner,'a.pdf','a.pdf','processing')"),{'id':doc,'owner':owner})
    job=await create_ingestion_job(db_session,doc)
    await db_session.commit()
    monkeypatch.setattr(failures,'queue_alert',Mock())
    row=failures.record_failure(task_name,'failed',RuntimeError('provider down'),document_id=doc,job_id=job['id'] if 'ingestion' in task_name else None)
    def refail(*args,**kwargs):
        failures.record_failure(task_name,'retry-failed',RuntimeError('provider down'),document_id=doc,job_id=job['id'] if 'ingestion' in task_name else None)
        return Mock()
    monkeypatch.setattr(tasks.process_ingestion if 'ingestion' in task_name else tasks.embed_document,'delay' if 'ingestion' in task_name else 'apply_async',refail)
    spec=importlib.util.spec_from_file_location('dlq_fast_retry',Path('scripts/dlq.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    await module.retry(row['id'])
    assert await db_session.scalar(text('SELECT status FROM failed_jobs WHERE id=:id'),{'id':row['id']})=='open'
    assert await db_session.scalar(text('SELECT attempts FROM failed_jobs WHERE id=:id'),{'id':row['id']})==2


def test_backfill_dry_run_and_apply(db_session_sync,tmp_path,monkeypatch,capsys):
    import hashlib
    import importlib.util
    from pathlib import Path
    session=db_session_sync
    doc,owner=uuid4(),uuid4()
    session.execute(text("INSERT INTO users(id,email,password_hash) VALUES (:id,'hash@example.test','test')"),{'id':owner})
    session.execute(text("INSERT INTO documents(id,user_id,filename,original_filename,status) VALUES (:id,:owner,'a.pdf','a.pdf','complete')"),{'id':doc,'owner':owner})
    session.commit()
    payload=b'%PDF-1.7\nq1 backfill'
    (tmp_path/'a.pdf').write_bytes(payload)
    monkeypatch.setattr('app.core.paths.documents_dir',lambda:tmp_path)
    spec=importlib.util.spec_from_file_location('hash_script',Path('scripts/backfill_content_hash.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.main(['--dry-run'])
    assert session.scalar(text('SELECT content_sha256 FROM documents WHERE id=:id'),{'id':doc}) is None
    session.commit()
    module.main(['--apply'])
    assert session.scalar(text('SELECT content_sha256 FROM documents WHERE id=:id'),{'id':doc})==hashlib.sha256(payload).hexdigest()


def test_non_http_credentials_scrubbed():
    value=failures.scrub('redis://test-user:synthetic-secret@cache.test/0 postgresql://test-user:synthetic-secret@db.test/x')
    assert 'synthetic-secret' not in value
    assert 'test-user:' not in value


def test_alert_cap_remains_until_morning_summary(monkeypatch):
    from datetime import datetime
    class Morning(datetime):
        @classmethod
        def now(cls, zone):
            return cls(2026,10,6,4,tzinfo=zone)
    monkeypatch.setattr(failures,'datetime',Morning)
    client=Mock()
    failures.admit_alert(client,'fingerprint')
    assert client.eval.call_args.args[3]=='q1:alert:cap:2026-10-05'


def test_provider_4xx_is_system():
    import httpx
    from app.api.errors import ModelUnavailable
    response=httpx.Response(404,request=httpx.Request('POST','https://provider.example.test/embed'))
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        try:
            raise ModelUnavailable('embedding model not configured') from error
        except ModelUnavailable as wrapped:
            assert failures.classify_failure(wrapped)[0]=='system'


def test_queued_job_waiting_for_long_book_is_not_stalled(db_session_sync,monkeypatch):
    monkeypatch.setattr(failures,'queue_alert',Mock())
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET progress_updated_at=now()-interval '1 day' WHERE id=:id"),{'id':job})
    db_session_sync.commit()
    assert failures.sweep_stalled(active_ids=set(),inspection_ok=True)==0


def test_sweep_rechecks_later_heartbeat(db_session_sync,monkeypatch):
    session=db_session_sync
    monkeypatch.setattr(failures,'queue_alert',Mock())
    docs=[uuid4(),uuid4()];jobs=[uuid4(),uuid4()]
    for doc,job in zip(docs,jobs):
        _insert_document_and_job(session,doc,job)
        session.execute(text("UPDATE ingestion_jobs SET status='extracting',progress_updated_at=now()-interval '1 day' WHERE id=:id"),{'id':job})
    session.commit()
    original=failures.record_failure
    recorded=[]
    def record(*args,**kwargs):
        recorded.append(kwargs['job_id'])
        result=original(*args,**kwargs)
        if len(recorded)==1:
            # Use the same transaction to emulate a later candidate's fresh
            # heartbeat between candidates without competing held locks.
            target=jobs[0] if str(recorded[0])==str(jobs[1]) else jobs[1]
            active_session=kwargs.get('session') or session
            active_session.execute(text('UPDATE ingestion_jobs SET progress_updated_at=clock_timestamp() WHERE id=:id'),{'id':target})
            if active_session is session: session.commit()
        return result
    monkeypatch.setattr(failures,'record_failure',record)
    assert failures.sweep_stalled(active_ids=set(),inspection_ok=True)==1


def test_maintenance_refailure_stable_identity(db_session_sync,monkeypatch):
    monkeypatch.setattr(failures,'queue_alert',Mock())
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    first=failures.record_failure('9xaipal.generate_article_thumbnail','one',RuntimeError('provider down'),document_id=doc)
    second=failures.record_failure('9xaipal.generate_article_thumbnail','two',RuntimeError('provider down'),document_id=doc)
    assert first['id']==second['id']
    assert second['attempts']==2


def test_progress_heartbeat_requires_observed_work(monkeypatch):
    import time
    from app.workers import progress_heartbeat as progress
    bump=Mock()
    monkeypatch.setattr(progress,'bump',bump)
    counter=[0]
    with progress.progress_heartbeat(uuid4(),interval=.02,sample=lambda:counter[0]):
        time.sleep(.08)
        assert bump.call_count==0
        counter[0]=1
        time.sleep(.08)
        assert bump.call_count>=1


def test_direct_provider_http_failure_is_system():
    import httpx
    response=httpx.Response(401,request=httpx.Request('POST','https://provider.example.test/embed'))
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        assert failures.classify_failure(error,task_name='9xaipal.embed_document')[0]=='system'


def test_pending_successor_stage_not_stalled(db_session_sync,monkeypatch):
    monkeypatch.setattr(failures,'queue_alert',Mock())
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET status='embedding',progress_updated_at=now()-interval '1 day' WHERE id=:id"),{'id':job})
    db_session_sync.commit()
    assert failures.sweep_stalled(active_ids=set(),inspection_ok=True,pending_document_ids={str(doc)})==0


def test_active_successor_stage_identified_by_document(db_session_sync,monkeypatch):
    monkeypatch.setattr(failures,'queue_alert',Mock())
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET status='embedding',progress_updated_at=now()-interval '60 minutes' WHERE id=:id"),{'id':job})
    db_session_sync.commit()
    assert failures.sweep_stalled(active_ids={f'document:{doc}'},inspection_ok=True)==0


def test_sweeper_inspection_tracks_successor_document(db_session_sync,monkeypatch):
    from app.workers import reliability
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET status='embedding',celery_task_id='original',progress_updated_at=now()-interval '60 minutes' WHERE id=:id"),{'id':job})
    db_session_sync.commit()
    inspector=Mock()
    inspector.active.return_value={'worker':[{'id':'successor','name':'9xaipal.embed_document','args':[str(doc)],'kwargs':{}}]}
    inspector.reserved.return_value={'worker':[]}
    monkeypatch.setattr(reliability.celery_app.control,'inspect',Mock(return_value=inspector))
    monkeypatch.setattr(failures,'queue_alert',Mock())
    assert reliability.sweep_stalled_jobs.run()==0


def test_retried_pdf_stamps_current_generation_at_start(db_session_sync,tmp_path,monkeypatch):
    from app.workers import tasks
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET execution_generation=1,status='extracting',progress_updated_at=now()-interval '1 day' WHERE id=:id"),{'id':job})
    db_session_sync.commit()
    monkeypatch.setenv('WORKER_ROLE','ingest')
    monkeypatch.setattr(tasks,'documents_dir',lambda:tmp_path)
    monkeypatch.setattr(tasks,'check_disk_headroom',lambda:None)
    monkeypatch.setattr(tasks,'_queue_search_vector_if_embedding_skipped',Mock())
    (tmp_path/'retry.pdf').write_bytes(b'%PDF-1.7\n')
    def pipeline(*args,**kwargs):
        assert tasks.process_ingestion.request.reliability_generation==1
        assert db_session_sync.scalar(text("SELECT progress_updated_at>now()-interval '1 minute' FROM ingestion_jobs WHERE id=:id"),{'id':job})
    monkeypatch.setattr(tasks,'run_pipeline_sync',pipeline)
    tasks.process_ingestion.run(str(doc),str(job),'retry.pdf',execution_generation=1)


def test_sweeper_refreshes_inspection_after_broker_snapshot(db_session_sync,monkeypatch):
    from app.workers import reliability
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET status='embedding',progress_updated_at=now()-interval '60 minutes' WHERE id=:id"),{'id':job})
    db_session_sync.commit()
    inspector=Mock()
    inspector.active.side_effect=[{'worker':[]},{'worker':[{'id':'dequeued','name':'9xaipal.embed_document','args':[str(doc)],'kwargs':{}}]}]
    inspector.reserved.return_value={'worker':[]}
    monkeypatch.setattr(reliability.celery_app.control,'inspect',Mock(return_value=inspector))
    monkeypatch.setattr(reliability,'_pending_documents',lambda:set())
    monkeypatch.setattr(failures,'queue_alert',Mock())
    assert reliability.sweep_stalled_jobs.run()==0
    assert inspector.active.call_count==2
