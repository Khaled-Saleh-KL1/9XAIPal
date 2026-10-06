from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import Mock, MagicMock
from uuid import uuid4

import httpx
import pytest
import redis
from sqlalchemy import text

from app.core.config import settings

@pytest.mark.parametrize('error,category', [
    (ValueError('corrupt PDF'), 'user_input'),
    (ValueError('encrypted PDF'), 'user_input'),
    (ValueError('zero pages'), 'user_input'),
    (ValueError('No chunks extracted from document'), 'user_input'),
    (ValueError('unsupported file type'), 'user_input'),
    (ValueError('file too large'), 'user_input'),
    (RuntimeError('provider unavailable'), 'system'),
    (MemoryError('out of memory'), 'system'),
    (RuntimeError('database disconnected'), 'system'),
])
def test_classifier(error, category):
    from app.services.failures import classify_failure
    assert classify_failure(error)[0] == category

@pytest.mark.parametrize('status,category', [(404, 'user_input'), (503, 'system')])
def test_http_classifier(status, category):
    from app.services.failures import classify_failure
    response = httpx.Response(status, request=httpx.Request('GET', 'https://example.test'))
    with pytest.raises(httpx.HTTPStatusError) as error:
        response.raise_for_status()
    assert classify_failure(error.value)[0] == category


def test_scrub_and_fingerprint():
    from app.services.failures import scrub, fingerprint
    value = scrub('Authorization: Bearer example-token\napi_key=example-key https://user:pass@example.test/x?token=private')
    assert all(secret not in value for secret in ['example-token', 'example-key', 'user:pass', 'private'])
    assert fingerprint('task', 'Error', 'failed /tmp/a.pdf 123 '+str(uuid4())) == fingerprint('task','Error','failed /var/b.pdf 456 '+str(uuid4()))


def test_terminal_failure_once_and_refailure(db_session_sync, monkeypatch):
    from app.services import failures
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    session = db_session_sync
    session.execute(text('TRUNCATE failed_jobs CASCADE'))
    session.commit()
    first = failures.record_failure('9xaipal.test', 'delivery', RuntimeError('provider down'))
    again = failures.record_failure('9xaipal.test', 'delivery', RuntimeError('provider down'))
    assert first['id'] == again['id']
    assert again['attempts'] == 2
    assert session.execute(text('SELECT count(*) FROM failed_jobs')).scalar() == 1


def test_retry_then_success_not_recorded(db_session_sync, monkeypatch):
    from app.core.celery_app import celery_app
    from app.services import failures
    import app.workers.reliability  # install signal receivers
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    db_session_sync.execute(text('TRUNCATE failed_jobs CASCADE'))
    db_session_sync.commit()
    @celery_app.task(bind=True, name='9xaipal.test_retry_success', max_retries=1)
    def succeeds(task):
        if task.request.retries == 0:
            raise task.retry(exc=RuntimeError('temporary'))
        return 'ok'
    assert succeeds.apply().get() == 'ok'
    assert db_session_sync.execute(text('SELECT count(*) FROM failed_jobs')).scalar() == 0


def test_exhausted_retry_signal(db_session_sync, monkeypatch):
    from app.core.celery_app import celery_app
    from app.services import failures
    import app.workers.reliability
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    db_session_sync.execute(text('TRUNCATE failed_jobs CASCADE'))
    db_session_sync.commit()
    @celery_app.task(bind=True, name='9xaipal.test_exhausted', max_retries=1)
    def fails(task):
        raise task.retry(exc=RuntimeError('provider down'))
    assert fails.apply().failed()
    assert db_session_sync.execute(text('SELECT attempts FROM failed_jobs')).scalar() == 1


def test_alert_gate_real_redis(db_session_sync, monkeypatch):
    from app.services import failures
    client = failures.alert_client()
    client.flushdb()
    monkeypatch.setattr(settings, 'alert_max_emails_per_day', 2)
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.test')
    monkeypatch.setattr(settings, 'alert_email_to', 'owner@example.test')
    monkeypatch.setattr(settings, 'alert_email_from', 'alerts@example.test')
    monkeypatch.setattr('smtplib.SMTP', MagicMock())
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    rows = [failures.record_failure('9xaipal.gate', str(uuid4()), RuntimeError(message))
            for message in ('alpha outage','alpha outage','beta outage','gamma outage')]
    for row in rows:
        failures.send_row_alert(row['id'])
    assert db_session_sync.scalar(text('SELECT count(*) FROM failed_jobs WHERE notified_at IS NOT NULL')) == 2
    assert sum(int(client.get(k) or 0) for k in client.scan_iter('q1:alert:cap:*')) == 2
    client.flushdb()
    rows = [failures.record_failure('9xaipal.gate_concurrent', str(uuid4()), RuntimeError('simultaneous outage')) for _ in range(2)]
    barrier = Barrier(2)
    def call(row):
        barrier.wait()
        failures.send_row_alert(row['id'])
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(call, rows))
    assert db_session_sync.scalar(text("SELECT count(*) FROM failed_jobs WHERE task_name='9xaipal.gate_concurrent' AND notified_at IS NOT NULL")) == 1
    client.flushdb()
    client.close()


def test_smtp_starttls_and_disabled(monkeypatch):
    from app.services.failures import send_email
    smtp = MagicMock()
    monkeypatch.setattr('smtplib.SMTP', smtp)
    monkeypatch.setattr(settings, 'smtp_host', '')
    assert send_email('subject', 'body') is False
    smtp.assert_not_called()
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.test')
    monkeypatch.setattr(settings, 'alert_email_to', 'owner@example.test')
    monkeypatch.setattr(settings, 'alert_email_from', 'alerts@example.test')
    assert send_email('subject', 'body')
    smtp.return_value.__enter__.return_value.starttls.assert_called_once()
    smtp.return_value.__enter__.return_value.send_message.assert_called_once()


def test_stalled_vs_long(db_session_sync, monkeypatch):
    from app.services import failures
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    session = db_session_sync
    doc = uuid4()
    session.execute(text("INSERT INTO documents (id,filename,original_filename,status) VALUES (:id,'a.pdf','a.pdf','processing')"), {'id':doc})
    job = session.execute(text("INSERT INTO ingestion_jobs(document_id,status,progress_updated_at) VALUES (:doc,'extracting',now()) RETURNING id"), {'doc':doc}).scalar()
    session.commit()
    assert failures.sweep_stalled(active_ids=set(), inspection_ok=True) == 0
    session.execute(text("UPDATE ingestion_jobs SET progress_updated_at=now()-interval '60 minutes' WHERE id=:id"), {'id':job})
    session.commit()
    # Unknown inspection and a reported live task are both conservatively safe.
    assert failures.sweep_stalled(active_ids=set(), inspection_ok=False) == 0
    session.execute(text("UPDATE ingestion_jobs SET execution_task_id='live' WHERE id=:id"), {'id':job})
    session.commit()
    assert failures.sweep_stalled(active_ids={'live'}, inspection_ok=True) == 0
    assert failures.sweep_stalled(active_ids=set(), inspection_ok=True) == 1
    assert session.execute(text('SELECT status FROM documents WHERE id=:id'), {'id':doc}).scalar() == 'failed'
    assert session.execute(text("SELECT category FROM failed_jobs WHERE document_id=:id"), {'id':doc}).scalar() == 'stalled'


def test_job_progress_moves_heartbeat_but_lease_renew_does_not_fake_progress(db_session_sync):
    from app.extraction.pipeline_sync import update_job_progress_sync
    from app.workers.execution_claims import ExecutionClaim
    session = db_session_sync
    doc = uuid4()
    session.execute(text("INSERT INTO documents(id,filename,original_filename,status) VALUES (:id,'a.pdf','a.pdf','processing')"), {'id':doc})
    job = session.execute(text("INSERT INTO ingestion_jobs(document_id,status,progress_updated_at) VALUES (:doc,'queued',now()-interval '2 hours') RETURNING id"), {'doc':doc}).scalar()
    session.commit()
    update_job_progress_sync(session,job,.2)
    session.commit()
    old = session.execute(text('SELECT progress_updated_at FROM ingestion_jobs WHERE id=:id'), {'id':job}).scalar()
    session.commit()
    with ExecutionClaim('ingestion',job,'test-heartbeat') as claim:
        assert claim.renew()
        current = session.execute(text('SELECT progress_updated_at FROM ingestion_jobs WHERE id=:id'), {'id':job}).scalar()
        assert current == old


def test_stalled_live_task_at_three_thresholds(db_session_sync, monkeypatch):
    from app.services import failures
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    session = db_session_sync
    doc=uuid4()
    session.execute(text("INSERT INTO documents(id,filename,original_filename,status) VALUES (:id,'a.pdf','a.pdf','processing')"), {'id':doc})
    session.execute(text("INSERT INTO ingestion_jobs(document_id,status,execution_task_id,progress_updated_at) VALUES (:doc,'extracting','frozen',now()-interval '4 hours')"), {'doc':doc})
    session.commit()
    assert failures.sweep_stalled(active_ids={'frozen'},inspection_ok=True)==1


def test_summary_counts_and_once_per_day(db_session_sync, monkeypatch):
    from app.services import failures
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    send = Mock(return_value=True)
    monkeypatch.setattr(failures,'send_email',send)
    failures.alert_client().flushdb()
    db_session_sync.execute(text('TRUNCATE failed_jobs CASCADE'))
    db_session_sync.commit()
    failures.record_failure('9xaipal.test','input',ValueError('corrupt PDF'))
    # Open rows qualify even when their first failure is after the 08:00 window.
    failures.daily_summary()
    failures.daily_summary()
    assert send.call_count == 1
    assert 'user_input' in send.call_args.args[1]


def test_completed_document_and_obsolete_job_do_not_run(db_session_sync, monkeypatch):
    from app.workers import tasks
    from celery.exceptions import Ignore
    from test_article_ingestion import _insert_document_and_job
    session=db_session_sync
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(session,doc,job)
    session.execute(text("UPDATE documents SET status='complete' WHERE id=:id"), {'id':doc})
    session.commit()
    monkeypatch.setenv('WORKER_ROLE','ingest')
    pipeline=Mock()
    monkeypatch.setattr(tasks,'run_pipeline_sync',pipeline)
    with pytest.raises(Ignore):
        tasks.process_ingestion.run(str(doc),str(job),'missing.pdf')
    pipeline.assert_not_called()
    session.execute(text("UPDATE documents SET status='processing' WHERE id=:id"), {'id':doc})
    session.execute(text("INSERT INTO ingestion_jobs(document_id,status) VALUES (:id,'queued')"), {'id':doc})
    session.commit()
    with pytest.raises(Ignore):
        tasks.process_ingestion.run(str(doc),str(job),'missing.pdf')
    pipeline.assert_not_called()


def test_summary_uses_period_events_not_lifetime_attempts(db_session_sync,monkeypatch):
    from app.services import failures
    monkeypatch.setattr(failures,'queue_alert',Mock())
    send=Mock(return_value=True)
    monkeypatch.setattr(failures,'send_email',send)
    failures.alert_client().flushdb()
    session=db_session_sync
    session.execute(text('TRUNCATE failed_jobs CASCADE'))
    session.commit()
    row=failures.record_failure('9xaipal.test','historic',RuntimeError('old'))
    session.execute(text("UPDATE failed_jobs SET attempts=100,last_failed_at=now()-interval '1 day' WHERE id=:id"), {'id':row['id']})
    session.commit()
    failures.daily_summary()
    assert "'count': 100" not in send.call_args.args[1]


@pytest.mark.parametrize('message', [
    'No structural chunks extracted from document',
    'MinerU extraction produced no markdown output',
])
def test_actual_empty_extraction_records_input_without_alert(db_session_sync, monkeypatch, message):
    from app.services import failures
    from app.extraction.mineru_client import MinerUError
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    row = failures.record_failure('9xaipal.process_ingestion', str(uuid4()), MinerUError(message))
    assert row['category'] == 'user_input'
    assert 'No readable text' in row['error_message']
    failures.queue_alert.assert_not_called()


def test_smtp_failure_can_retry_without_spending_success_cap(db_session_sync, monkeypatch):
    from app.services import failures
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    monkeypatch.setattr(settings, 'alert_max_emails_per_day', 1)
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.test')
    monkeypatch.setattr(settings, 'alert_email_to', 'owner@example.test')
    monkeypatch.setattr(settings, 'alert_email_from', 'alerts@example.test')
    client = failures.alert_client()
    client.flushdb()
    row = failures.record_failure('9xaipal.test_smtp', str(uuid4()), RuntimeError('outage'))
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value.send_message.side_effect = [TimeoutError('temporary SMTP outage'), None]
    monkeypatch.setattr('smtplib.SMTP', smtp)
    with pytest.raises(TimeoutError):
        failures.send_row_alert(row['id'])
    assert sum(int(client.get(k) or 0) for k in client.scan_iter('q1:alert:cap:*')) == 0
    failures.send_row_alert(row['id'])
    assert db_session_sync.scalar(text('SELECT notified_at FROM failed_jobs WHERE id=:id'), {'id':row['id']}) is not None
    assert sum(int(client.get(k) or 0) for k in client.scan_iter('q1:alert:cap:*')) == 1
    failures.send_row_alert(row['id'])
    assert smtp.return_value.__enter__.return_value.send_message.call_count == 2
    client.flushdb()
