"""Heavy legacy/misrouted deliveries never extract in the light process."""
import base64
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.celery_app import celery_app, _restore_interrupted_tasks
from app.workers import tasks
from test_article_ingestion import _insert_document_and_job, _pdf_resource
from test_worker_restore import _fake_unacked, _worker_sender


@pytest.fixture
def broker():
    with celery_app.connection_for_write() as connection:
        channel = connection.default_channel
        client = channel.client
        client.delete("ingest", "celery")
        yield channel, client
        client.delete("ingest", "celery")
        # Receipts are unique per test delivery; never delete other receipts.
        for key in client.scan_iter("9xaipal:forward:test-M2-*"):
            client.delete(key)


def _decode(payload):
    message = json.loads(payload)
    args, kwargs, _ = json.loads(base64.b64decode(message["body"]))
    return message, args, kwargs


@pytest.mark.parametrize("name", ["process_ingestion", "reconstruct_reading_order"])
@pytest.mark.parametrize("source", ["queued", "restored"])
def test_light_forwards_heavy_delivery_once_without_work(name, source, broker, db_session_sync, tmp_path, monkeypatch):
    channel, client = broker
    task = getattr(tasks, name)
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    task_id = f"test-M2-{uuid4()}"
    args = [str(doc), str(job), "x.pdf"] if name == "process_ingestion" else [str(doc)]
    if source == "restored":
        tag = _fake_unacked(channel, client, task.name, args, task_id=task_id)
        _restore_interrupted_tasks(sender=_worker_sender("celery"))
        message, args, kwargs = _decode(client.rpop("celery"))
        assert not client.hexists(channel.unacked_key, tag)
        task_id = message["headers"]["id"]
    else:
        # Include kwargs: forwarding must preserve their shape as well as ids.
        kwargs = {"filename": args.pop()} if name == "process_ingestion" else {}
        task.apply_async(args=args, kwargs=kwargs, task_id=task_id, queue="celery")
        message, args, kwargs = _decode(client.rpop("celery"))
    monkeypatch.setenv("WORKER_ROLE", "light")
    monkeypatch.setattr(tasks, "documents_dir", lambda: tmp_path)
    before = db_session_sync.execute(text("SELECT status FROM ingestion_jobs WHERE id=:id"), {"id": job}).scalar_one()
    with patch.object(tasks, "run_pipeline_sync") as pipeline, \
         patch.object(tasks, "reconstruct_reading_order_for_document") as reorder, \
         patch.object(tasks, "sync_session") as session, \
         patch.object(tasks.sync_engine, "dispose") as dispose:
        for redelivered in (False, True):
            result = task.apply(args=args, kwargs=kwargs, task_id=task_id,
                                delivery_info={"redelivered": redelivered}).get()
            assert result["status"] == "forwarded"
        pipeline.assert_not_called()
        reorder.assert_not_called()
        session.assert_not_called()
        dispose.assert_not_called()
    assert list(tmp_path.iterdir()) == []
    assert db_session_sync.execute(text("SELECT status FROM ingestion_jobs WHERE id=:id"), {"id": job}).scalar_one() == before
    assert db_session_sync.execute(text("SELECT status FROM documents WHERE id=:id"), {"id": doc}).scalar_one() == "queued"
    assert client.llen("ingest") == 1
    forwarded, forwarded_args, forwarded_kwargs = _decode(client.rpop("ingest"))
    assert forwarded["headers"]["task"] == task.name
    # A light-task result must not overwrite the heavy execution result.
    assert forwarded["headers"]["id"] != task_id
    assert forwarded_args == args and forwarded_kwargs == kwargs
    client.delete(f"9xaipal:forward:{task_id}:0")


def test_ingest_role_runs_pipeline(broker, db_session_sync, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKER_ROLE", "ingest")
    monkeypatch.setattr(tasks, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(tasks, "check_disk_headroom", lambda: None)
    (tmp_path / "x.pdf").write_bytes(b"%PDF-")
    doc, job = uuid4(), uuid4()
    with patch.object(tasks, "run_pipeline_sync") as pipeline:
        result = tasks.process_ingestion.apply(args=[str(doc), str(job), "x.pdf"]).get()
    pipeline.assert_called_once()
    assert pipeline.call_args.kwargs == {"document_id": doc, "job_id": job, "pdf_path": tmp_path / "x.pdf"}
    assert result["status"] == "complete"
    assert broker[1].llen("ingest") == 0


def test_ingest_role_reconstructs(broker, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setenv("WORKER_ROLE", "ingest")
    doc = uuid4()
    with patch.object(tasks, "reconstruct_reading_order_for_document", new_callable=AsyncMock, return_value={"reordered": 2}) as reorder:
        result = tasks.reconstruct_reading_order.apply(args=[str(doc)]).get()
    assert reorder.await_args.args[1] == doc
    assert result == {"document_id": str(doc), "reordered": 2}
    assert broker[1].llen("ingest") == 0


def test_forwarded_deleted_document_keeps_pipeline_guard(broker, tmp_path, monkeypatch):
    from app.extraction import pipeline_sync
    monkeypatch.setattr(tasks, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(tasks, "check_disk_headroom", lambda: None)
    (tmp_path / "x.pdf").write_bytes(b"%PDF-")
    monkeypatch.setenv("WORKER_ROLE", "light")
    task_id = f"test-M2-{uuid4()}"
    tasks.process_ingestion.apply(args=[str(uuid4()), str(uuid4()), "x.pdf"], task_id=task_id).get()
    message, args, kwargs = _decode(broker[1].rpop("ingest"))
    monkeypatch.setenv("WORKER_ROLE", "ingest")
    with patch.object(pipeline_sync, "extract_pdf_sync") as extract:
        tasks.process_ingestion.apply(args=args, kwargs=kwargs, task_id=message["headers"]["id"]).get()
    extract.assert_not_called()


def test_pdf_redirect_publishes_to_real_ingest_queue(broker, db_session_sync, tmp_path, monkeypatch):
    from app.extraction import pipeline_sync as ps
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    monkeypatch.setattr(ps, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "assets_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "ensure_storage_dirs", lambda: None)
    monkeypatch.setenv("WORKER_ROLE", "light")
    with patch("app.services.article_extraction.fetch_resource", return_value=_pdf_resource("https://publisher.example/final.pdf")), \
         patch.object(ps, "run_pipeline_sync") as inline:
        assert ps.run_article_pipeline_sync(db_session_sync, document_id=doc, job_id=job, url="https://doi.org/example") is False
    inline.assert_not_called()
    assert broker[1].llen("celery") == 0
    message, args, kwargs = _decode(broker[1].rpop("ingest"))
    assert message["headers"]["task"] == tasks.process_ingestion.name
    assert args == [str(doc), str(job), f"{doc}.pdf"] and kwargs == {}
    row = db_session_sync.execute(text("SELECT doc_kind, original_filename FROM documents WHERE id=:id"), {"id": doc}).mappings().one()
    assert row == {"doc_kind": "paper", "original_filename": "final.pdf"}


def test_failed_forward_is_requeued_and_can_be_published_later(broker, monkeypatch):
    from celery.exceptions import Reject
    monkeypatch.setenv("WORKER_ROLE", "light")
    task_id = f"test-M2-{uuid4()}"
    args = [str(uuid4()), str(uuid4()), "x.pdf"]
    tasks.process_ingestion.push_request(id=task_id, args=args, kwargs={}, retries=0)
    try:
        with patch.object(tasks.process_ingestion, "apply_async", side_effect=RuntimeError("broker unavailable")):
            with pytest.raises(Reject) as exc:
                tasks.process_ingestion.run(*args)
        assert exc.value.requeue is True
        assert not broker[1].exists(f"9xaipal:forward:{task_id}:0")
        assert broker[1].llen("ingest") == 0
    finally:
        tasks.process_ingestion.pop_request()
    assert tasks.process_ingestion.apply(args=args, task_id=task_id).get()["status"] == "forwarded"
    assert broker[1].llen("ingest") == 1


def test_connection_failure_before_publication_is_requeued(broker, monkeypatch):
    from celery.exceptions import Reject
    monkeypatch.setenv("WORKER_ROLE", "light")
    task_id = f"test-M2-{uuid4()}"
    args = [str(uuid4()), str(uuid4()), "x.pdf"]
    tasks.process_ingestion.push_request(id=task_id, args=args, kwargs={}, retries=0)
    try:
        with patch.object(celery_app, "connection_for_write", side_effect=RuntimeError("cannot connect")):
            with pytest.raises(Reject) as exc:
                tasks.process_ingestion.run(*args)
        assert exc.value.requeue is True
        assert broker[1].llen("ingest") == 0
    finally:
        tasks.process_ingestion.pop_request()


def test_concurrent_redeliveries_publish_only_one_ingest_run(broker, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv("WORKER_ROLE", "light")
    task_id = f"test-M2-{uuid4()}"
    args = [str(uuid4()), str(uuid4()), "x.pdf"]
    def forward(_):
        return tasks.process_ingestion.apply(args=args, task_id=task_id).get()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(r["status"] == "forwarded" for r in pool.map(forward, range(2)))
    assert broker[1].llen("ingest") == 1
    monkeypatch.setenv("WORKER_ROLE", "ingest")
    monkeypatch.setattr(tasks, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(tasks, "check_disk_headroom", lambda: None)
    (tmp_path / "x.pdf").write_bytes(b"%PDF-")
    message, forwarded_args, kwargs = _decode(broker[1].rpop("ingest"))
    with patch.object(tasks, "run_pipeline_sync") as pipeline:
        tasks.process_ingestion.apply(args=forwarded_args, kwargs=kwargs, task_id=message["headers"]["id"]).get()
    pipeline.assert_called_once()
    assert broker[1].llen("ingest") == 0
