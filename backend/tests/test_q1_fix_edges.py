"""Lease loss and lock-order regressions found in the fix-round review."""
import asyncio
import importlib.util
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.services import failures
from test_article_ingestion import _insert_document_and_job
from test_q1_uploads import client, upload, PDF, documents


@pytest.mark.parametrize('renewal_error', [False, True])
async def test_expired_body_lease_stops_before_original_storage(client, db_session, monkeypatch, renewal_error):
    writes = []
    original = documents._stream_pdf_upload
    async def stream(*args, **kwargs):
        writes.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(documents, '_stream_pdf_upload', stream)
    async def body():
        if renewal_error:
            async def unavailable(token):
                raise OSError('test-only database renewal failure')
            monkeypatch.setattr('app.services.upload_admission.renew_upload', unavailable)
        else:
            await db_session.execute(text("UPDATE upload_reservations SET expires_at=now()-interval '1 second'"))
            await db_session.commit()
        yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\nContent-Type: application/pdf\r\n\r\n'+PDF+b'\r\n--boundary--\r\n'
    response = await client.post('/api/v1/documents/upload', content=body(), headers={'Content-Type':'multipart/form-data; boundary=boundary'})
    assert response.status_code == 503
    assert response.json()['code'] == 'service_unavailable'
    assert writes == []
    assert await db_session.scalar(text('SELECT count(*) FROM upload_reservations')) == 0


async def test_failed_key_mismatch_is_validated_at_capacity(client, db_session, monkeypatch):
    key = str(uuid4())
    first = await upload(client, key=key)
    await db_session.execute(text("UPDATE documents SET status='failed' WHERE id=:id"), {'id':first.json()['id']})
    await db_session.execute(text("UPDATE ingestion_jobs SET status='failed' WHERE document_id=:id"), {'id':first.json()['id']})
    await db_session.commit()
    assert (await client.post('/api/v1/documents/import-url', json={'url':'https://example.test/article'})).status_code == 201
    monkeypatch.setattr(settings, 'max_queued_ingestion_jobs', 1)
    mismatch = await upload(client, PDF+b'other', key=key)
    assert mismatch.status_code == 422
    assert (await upload(client, key=key)).status_code == 429


def test_sweeper_skips_document_locked_by_terminal_failure(db_session_sync, monkeypatch):
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET status='extracting',progress_updated_at=now()-interval '1 day' WHERE id=:id"), {'id':job})
    db_session_sync.commit()
    db_session_sync.execute(text('SELECT id FROM documents WHERE id=:id FOR UPDATE'), {'id':doc})
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(failures.sweep_stalled, active_ids=set(), inspection_ok=True)
        try:
            wait([future], timeout=.5)
            assert future.done(), 'Sweeper held the job while waiting for a locked document'
            assert future.result() == 0
        finally:
            db_session_sync.rollback()
            future.result(timeout=5)


async def test_cli_retry_locks_document_before_failure_row(db_session, monkeypatch):
    spec = importlib.util.spec_from_file_location('dlq_fix_test', Path('scripts/dlq.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    doc, job = uuid4(), uuid4()
    from app.database.connection import sync_session
    with sync_session() as db:
        _insert_document_and_job(db, doc, job)
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    row = failures.record_failure('9xaipal.process_ingestion',str(uuid4()),RuntimeError('outage'),document_id=doc,job_id=job)
    monkeypatch.setattr(settings, 'ingestion_disk_refuse_percent', 101)
    monkeypatch.setattr(documents.process_ingestion, 'delay', Mock())
    await db_session.execute(text('SELECT id FROM documents WHERE id=:id FOR UPDATE'), {'id':doc})
    retry = asyncio.create_task(module.retry(row['id']))
    try:
        # Wait until the retry really blocks on our row, avoiding a scheduler guess.
        for _ in range(100):
            await db_session.execute(text('SELECT pg_stat_clear_snapshot()'))
            blocked = await db_session.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%FROM documents%FOR UPDATE%')"))
            if blocked:
                break
            await asyncio.sleep(.01)
        assert blocked
        await db_session.execute(text('SELECT id FROM failed_jobs WHERE id=:id FOR UPDATE NOWAIT'), {'id':row['id']})
    finally:
        await db_session.rollback()
        await retry


def test_smtp_expired_cache_lease_never_allows_parallel_send(db_session_sync, monkeypatch):
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    monkeypatch.setattr(settings, 'alert_max_emails_per_day', 1)
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.test')
    monkeypatch.setattr(settings, 'alert_email_to', 'owner@example.test')
    monkeypatch.setattr(settings, 'alert_email_from', 'alerts@example.test')
    cache = failures.alert_client()
    cache.flushdb()
    rows = [failures.record_failure('9xaipal.slow_smtp',str(uuid4()),RuntimeError(message)) for message in ('alpha outage','beta outage')]
    started, release = Event(), Event()
    sends = []
    def smtp(subject, body):
        sends.append(subject)
        if len(sends) == 1:
            started.set()
            assert release.wait(5)
        return True
    monkeypatch.setattr(failures, 'send_email', smtp)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(failures.send_row_alert, rows[0]['id'])
        assert started.wait(5)
        # Simulate cache-lease expiry while SMTP is still legitimately active.
        for key in cache.scan_iter('q1:alert:delivery:*'):
            cache.delete(key)
        for key in cache.scan_iter('q1:alert:pending:*'):
            cache.delete(key)
        second = pool.submit(failures.send_row_alert, rows[1]['id'])
        try:
            wait([second], timeout=.2)
            assert len(sends) == 1
        finally:
            release.set()
            first.result(timeout=5)
            second.result(timeout=5)
    assert len(sends) == 1
    assert sum(int(cache.get(k) or 0) for k in cache.scan_iter('q1:alert:cap:*')) == 1
    assert db_session_sync.scalar(text("SELECT count(*) FROM failed_jobs WHERE task_name='9xaipal.slow_smtp' AND notified_at IS NOT NULL")) == 1
    cache.flushdb()


async def test_spooled_upload_lost_lease_stops_before_file_read(client, db_session, monkeypatch):
    reads = []
    original = documents._stream_pdf_upload
    async def stream(file, *args, **kwargs):
        await db_session.execute(text("UPDATE upload_reservations SET expires_at=now()-interval '1 second'"))
        await db_session.commit()
        read = file.read
        async def tracked_read(*args):
            reads.append(True)
            return await read(*args)
        file.read = tracked_read
        return await original(file, *args, **kwargs)
    monkeypatch.setattr(documents, '_stream_pdf_upload', stream)
    response = await upload(client)
    assert response.status_code == 503
    assert reads == []
    assert await db_session.scalar(text('SELECT count(*) FROM upload_reservations')) == 0


async def test_delayed_dispatch_failure_preserves_retry_generation(db_session, monkeypatch):
    from app.api.errors import UploadAdmissionError
    from app.database.connection import sync_session
    doc, job = uuid4(), uuid4()
    with sync_session() as db:
        _insert_document_and_job(db, doc, job)
        db.execute(text("UPDATE documents SET status='processing' WHERE id=:id"), {'id':doc})
        db.execute(text("UPDATE ingestion_jobs SET execution_generation=1,status='queued' WHERE id=:id"), {'id':job})
        db.commit()
    monkeypatch.setattr(documents.process_ingestion, 'delay', Mock(side_effect=RuntimeError('delayed publication failure')))
    monkeypatch.setattr(failures, 'queue_alert', Mock())
    with pytest.raises(UploadAdmissionError):
        await documents._dispatch_upload(db_session, {'id':doc,'filename':'a.pdf'}, {'id':job,'execution_generation':0})
    assert await db_session.scalar(text('SELECT status FROM documents WHERE id=:id'), {'id':doc}) == 'processing'
    assert await db_session.scalar(text('SELECT status FROM ingestion_jobs WHERE id=:id'), {'id':job}) == 'queued'
    assert await db_session.scalar(text('SELECT count(*) FROM failed_jobs')) == 0
    assert await db_session.scalar(text('SELECT count(*) FROM failure_events')) == 0
    failures.queue_alert.assert_not_called()
