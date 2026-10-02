"""Real Postgres consumer fencing, independent of Redis publication identity."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from celery.exceptions import Ignore, Reject
from sqlalchemy import text

from app.database.connection import sync_engine
from app.workers import tasks
from test_article_ingestion import _insert_document_and_job


def test_duplicate_deliveries_admit_one_pipeline(db_session_sync, tmp_path, monkeypatch):
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    monkeypatch.setenv("WORKER_ROLE", "ingest")
    monkeypatch.setattr(tasks, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(tasks, "check_disk_headroom", lambda: None)
    (tmp_path / "x.pdf").write_bytes(b"%PDF-")
    started, finish = Event(), Event()
    def pipeline(*args, **kwargs):
        started.set()
        assert finish.wait(10)
    with patch.object(tasks, "run_pipeline_sync", side_effect=pipeline) as run:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(tasks.process_ingestion.run, str(doc), str(job), "x.pdf")
            assert started.wait(10)
            try:
                with pytest.raises(Ignore):
                    tasks.process_ingestion.run(str(doc), str(job), "x.pdf")
            finally:
                finish.set()
            assert future.result()["status"] == "complete"
        with pytest.raises(Ignore):
            tasks.process_ingestion.run(str(doc), str(job), "x.pdf")
    assert run.call_count == 1


def test_expired_claim_recovery_and_token_fencing(db_session_sync):
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET execution_state='running', claim_token=:t, claim_expires_at=now()-interval '1 second' WHERE id=:id"), {"t": uuid4(), "id": job})
    db_session_sync.commit()
    with ExecutionClaim("ingestion", job, "delivery") as claim:
        assert claim.renew()
        db_session_sync.execute(text("UPDATE ingestion_jobs SET claim_token=:t WHERE id=:id"), {"t": uuid4(), "id": job})
        db_session_sync.commit()
        assert not claim.finish("complete")
    assert db_session_sync.execute(text("SELECT execution_state FROM ingestion_jobs WHERE id=:id"), {"id": job}).scalar_one() == "running"


def test_heartbeat_renews_during_blocking_step(db_session_sync, monkeypatch):
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    monkeypatch.setattr(ExecutionClaim, "renew_interval", .05)
    renewed = Event()
    original = ExecutionClaim.renew
    def renew(claim):
        result = original(claim)
        renewed.set()
        return result
    with patch.object(ExecutionClaim, "renew", renew):
        with ExecutionClaim("ingestion", job, "delivery"):
            assert renewed.wait(5)


def test_expired_lease_does_not_overlap_live_owner(db_session_sync):
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    with ExecutionClaim("ingestion", job, "first"):
        db_session_sync.execute(text("UPDATE ingestion_jobs SET claim_expires_at=now()-interval '1 second' WHERE id=:id"), {"id": job})
        db_session_sync.commit()
        with pytest.raises(Ignore):
            with ExecutionClaim("ingestion", job, "second"):
                pytest.fail("concurrent admission")


def test_crash_before_expiry_keeps_delivery_requeued(db_session_sync):
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    db_session_sync.execute(text("UPDATE ingestion_jobs SET execution_state='running', claim_token=:t, claim_expires_at=now()+interval '10 minutes' WHERE id=:id"), {"t": uuid4(), "id": job})
    db_session_sync.commit()
    with pytest.raises(Reject) as error:
        with ExecutionClaim("ingestion", job, "restored"):
            pytest.fail("unexpired claim admitted")
    assert error.value.requeue


def test_reading_order_claim_is_per_document_and_delivery(db_session_sync):
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    with ExecutionClaim("reading_order", doc, "first"):
        with pytest.raises(Ignore):
            with ExecutionClaim("reading_order", doc, "second"):
                pytest.fail("concurrent admission")
    with pytest.raises(Ignore):
        with ExecutionClaim("reading_order", doc, "first"):
            pytest.fail("duplicate completed delivery admitted")
    with ExecutionClaim("reading_order", doc, "new-user-request"):
        pass


def test_article_redelivery_reuses_pdf_while_ingest_runs(db_session_sync, tmp_path, monkeypatch):
    from app.extraction import pipeline_sync as ps
    from test_article_ingestion import _pdf_resource
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    monkeypatch.setattr(ps, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "assets_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "ensure_storage_dirs", lambda: None)
    monkeypatch.setattr(tasks, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(tasks, "check_disk_headroom", lambda: None)
    started, finish = Event(), Event()
    def pipeline(*args, **kwargs):
        started.set()
        assert finish.wait(10)
    # Commit to real Redis, then lose the publisher's reply. The article is
    # redelivered while the already-published ingest execution holds its claim.
    import base64
    import json
    from kombu import Connection
    import redis
    broker_url = "redis://host.docker.internal:55440/14"
    client = redis.Redis.from_url(broker_url)
    prefix = f"m3-pdf-{uuid4()}:"
    with Connection(broker_url, transport_options={"global_keyprefix": prefix}) as connection:
        original = tasks.process_ingestion.apply_async
        published = []
        def publish(**kwargs):
            result = original(connection=connection, **kwargs)
            published.append(result.id)
            if len(published) == 1:
                raise RuntimeError("reply lost after publication")
            return result
        try:
            with patch("app.services.article_extraction.fetch_resource", return_value=_pdf_resource("https://example.test/final.pdf")) as fetch, patch.object(tasks.process_ingestion, "apply_async", side_effect=publish), patch.object(tasks, "run_pipeline_sync", side_effect=pipeline) as run:
                with pytest.raises(Reject, match="reply lost") as rejected:
                    ps.run_article_pipeline_sync(db_session_sync, document_id=doc, job_id=job, url="https://example.test/pdf")
                assert rejected.value.requeue
                envelope = json.loads(client.rpop(prefix + "ingest"))
                args, _, _ = json.loads(base64.b64decode(envelope["body"]))
                path = tmp_path / f"{doc}.pdf"
                before = (path.read_bytes(), path.stat().st_mtime_ns)
                monkeypatch.setenv("WORKER_ROLE", "ingest")
                with ThreadPoolExecutor() as pool:
                    heavy = pool.submit(tasks.process_ingestion.run, *args)
                    assert started.wait(10)
                    try:
                        assert ps.run_article_pipeline_sync(db_session_sync, document_id=doc, job_id=job, url="https://example.test/pdf") is False
                        second = json.loads(client.rpop(prefix + "ingest"))
                        second_args, _, _ = json.loads(base64.b64decode(second["body"]))
                        with pytest.raises(Ignore):
                            tasks.process_ingestion.run(*second_args)
                        assert before == (path.read_bytes(), path.stat().st_mtime_ns)
                    finally:
                        finish.set()
                    heavy.result()
                fetch.assert_called_once()
                assert run.call_count == 1
        finally:
            keys = list(client.scan_iter(prefix + "*"))
            if keys:
                client.delete(*keys)


def test_adoption_never_overwrites_committed_pdf(db_session_sync, tmp_path, monkeypatch):
    from app.extraction import pipeline_sync as ps
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    monkeypatch.setattr(ps, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "assets_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "ensure_storage_dirs", lambda: None)
    ps._adopt_pdf_from_url(db_session_sync, document_id=doc, url="https://example.test/a.pdf", pdf=b"%PDF-first")
    path = tmp_path / f"{doc}.pdf"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    ps._adopt_pdf_from_url(db_session_sync, document_id=doc, url="https://example.test/a.pdf", pdf=b"%PDF-new")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_old_reading_order_redelivery_after_new_request_is_dropped(db_session_sync):
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    for delivery in ("first", "new-user-request"):
        with ExecutionClaim("reading_order", doc, delivery):
            pass
    with pytest.raises(Ignore):
        with ExecutionClaim("reading_order", doc, "first"):
            pytest.fail("old completed delivery admitted again")


def test_worker_process_crash_is_recoverable(db_session_sync, tmp_path):
    import subprocess
    import sys
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    process = subprocess.run([sys.executable, "-c", "from uuid import UUID; import os; from app.workers.execution_claims import ExecutionClaim; claim=ExecutionClaim('ingestion',UUID('" + str(job) + "'),'crashed'); claim.__enter__(); os._exit(4)"], capture_output=True, timeout=15)
    assert process.returncode == 4, process.stderr.decode()
    with pytest.raises(Reject):
        with ExecutionClaim("ingestion", job, "restored"):
            pytest.fail("unexpired crashed claim admitted")
    db_session_sync.execute(text("UPDATE ingestion_jobs SET claim_expires_at=now()-interval '1 second' WHERE id=:id"), {"id": job})
    db_session_sync.commit()
    with ExecutionClaim("ingestion", job, "restored"):
        pass
    assert db_session_sync.execute(text("SELECT execution_state FROM ingestion_jobs WHERE id=:id"), {"id": job}).scalar_one() == "complete"


def test_database_admission_failure_retains_source(monkeypatch):
    from app.workers.execution_claims import ExecutionClaim
    monkeypatch.setenv("WORKER_ROLE", "ingest")
    with patch.object(sync_engine, "connect", side_effect=RuntimeError("database unavailable")):
        with pytest.raises(Reject) as rejected:
            tasks.process_ingestion.run(str(uuid4()), str(uuid4()), "x.pdf")
    assert rejected.value.requeue


@pytest.mark.parametrize("task", [tasks.process_ingestion, tasks.reconstruct_reading_order, tasks.process_article_ingestion])
def test_prefork_child_loss_requeues_late_ack_work(task):
    assert task.acks_late
    assert task.reject_on_worker_lost is True


@pytest.mark.parametrize("late_response", ["failure", "html"])
def test_concurrent_article_source_cannot_corrupt_adopted_pdf(db_session_sync, tmp_path, monkeypatch, late_response):
    from app.database.connection import sync_session
    from app.extraction import pipeline_sync as ps
    from app.services.article_extraction import ArticleExtractionError
    from test_article_ingestion import _pdf_resource, _html_resource
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    monkeypatch.setattr(ps, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "assets_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "ensure_storage_dirs", lambda: None)
    first_fetch, second_fetch, release = Event(), Event(), Event()
    calls = []
    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            first_fetch.set()
            assert release.wait(10)
            return _pdf_resource()
        second_fetch.set()
        if late_response == "failure":
            raise ArticleExtractionError("late source failed")
        return _html_resource()
    def run():
        with sync_session() as session:
            return ps.run_article_pipeline_sync(session, document_id=doc, job_id=job, url="https://example.test/pdf")
    with patch("app.services.article_extraction.fetch_resource", side_effect=fetch), patch.object(tasks.process_ingestion, "apply_async"), patch.object(ps, "_finish_ingestion") as html_tail, patch.object(ps, "_handle_ingestion_failure") as cleanup:
        with ThreadPoolExecutor() as pool:
            first = pool.submit(run)
            assert first_fetch.wait(10)
            second = pool.submit(run)
            try:
                assert not second_fetch.wait(.3), "second source fetched without owning the article phase"
            finally:
                release.set()
            assert first.result() is False
            assert second.result() is False
        assert len(calls) == 1
        html_tail.assert_not_called()
        cleanup.assert_not_called()


def test_blocked_heartbeat_fails_closed_before_lease_expiry(db_session_sync):
    import subprocess
    import sys
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    script = """from uuid import UUID
import time
from app.workers.execution_claims import ExecutionClaim
ExecutionClaim.renew_interval = .03
ExecutionClaim.heartbeat_timeout = .15
ExecutionClaim.renew = lambda self: time.sleep(10)
with ExecutionClaim('ingestion', UUID('%s'), 'blocked-heartbeat'):
    time.sleep(10)
""" % job
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=4)
    assert result.returncode == 1, result.stderr.decode()


def test_checkpointed_failure_replays_original_exception_type(db_session_sync, monkeypatch):
    import json
    from app.workers.execution_claims import ExecutionClaim
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    claim = ExecutionClaim("ingestion", job, "crashed-before-errback")
    claim.__enter__()
    claim.save_outcome(None, json.dumps(tasks.process_ingestion.backend.prepare_exception(ValueError("typed failure"), serializer="json")))
    claim.abandon()
    db_session_sync.execute(text("UPDATE ingestion_jobs SET claim_expires_at=now()-interval '1 second' WHERE id=:id"), {"id": job})
    db_session_sync.commit()
    monkeypatch.setenv("WORKER_ROLE", "ingest")
    with patch.object(tasks, "run_pipeline_sync") as pipeline:
        with pytest.raises(ValueError, match="typed failure"):
            tasks.process_ingestion.apply(args=[str(doc), str(job), "missing.pdf"]).get()
    pipeline.assert_not_called()
    assert db_session_sync.execute(text("SELECT execution_state FROM ingestion_jobs WHERE id=:id"), {"id": job}).scalar_one() == "failed"


def test_new_reading_order_request_executes_instead_of_replaying_old_result(db_session_sync, monkeypatch):
    from unittest.mock import AsyncMock
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    monkeypatch.setenv("WORKER_ROLE", "ingest")
    with patch.object(tasks, "reconstruct_reading_order_for_document", new_callable=AsyncMock, side_effect=[{"reordered": 2}, {"reordered": 3}]) as reorder:
        first = tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=str(uuid4())).get()
        second = tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=str(uuid4())).get()
    assert first["reordered"] == 2
    assert second["reordered"] == 3
    assert reorder.await_count == 2
