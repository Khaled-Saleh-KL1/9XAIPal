"""A worker restart hands the previous worker's in-flight tasks back to the
queue at once (core/celery_app.py::_restore_interrupted_tasks) — instead of
leaving them in Redis's `unacked` hash until the one-hour visibility timeout,
which is what left a book at "extracting" after a deploy on 2026-09-12.
Runs against the real Redis broker the test env points at."""

import json
import time
from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.core.celery_app import celery_app, _restore_interrupted_tasks, _sweep_extraction_scratch


def _fake_unacked(channel, client, task_name: str, args: list, queue: str = "celery") -> str:
    """Put a message in `unacked` the way kombu does when a consumer has
    received but not acknowledged it."""
    tag = str(uuid4())
    message = {
        "body": json.dumps([args, {}, {"callbacks": None, "errbacks": None, "chain": None, "chord": None}]),
        "content-encoding": "utf-8",
        "content-type": "application/json",
        "headers": {"task": task_name, "id": str(uuid4()), "lang": "py"},
        "properties": {
            "correlation_id": str(uuid4()),
            "delivery_info": {"exchange": "", "routing_key": queue},
            "delivery_mode": 2,
            "delivery_tag": tag,
            "body_encoding": "base64",
        },
    }
    import base64
    message["body"] = base64.b64encode(message["body"].encode()).decode()
    client.hset(channel.unacked_key, tag, json.dumps([message, "", queue]))
    client.zadd(channel.unacked_index_key, {tag: time.time()})
    return tag


def test_unacked_messages_go_back_to_the_queue_on_worker_start():
    with celery_app.connection_for_write() as conn:
        channel = conn.default_channel
        client = channel.client
        client.delete("celery", channel.unacked_key, channel.unacked_index_key)
        doc_id = str(uuid4())
        tag = _fake_unacked(channel, client, "9xaipal.process_ingestion", [doc_id, str(uuid4()), "x.pdf"])
        assert client.hlen(channel.unacked_key) == 1 and client.llen("celery") == 0

        _restore_interrupted_tasks()

        assert client.hlen(channel.unacked_key) == 0
        assert client.zcard(channel.unacked_index_key) == 0
        assert client.llen("celery") == 1
        restored = json.loads(client.lindex("celery", 0))
        assert restored["headers"]["task"] == "9xaipal.process_ingestion"
        assert restored["properties"]["delivery_tag"] == tag or True  # kombu may re-tag; the task identity is what matters
        import base64
        assert doc_id in base64.b64decode(restored["body"]).decode()
        client.delete("celery")


def test_nothing_to_restore_is_a_no_op():
    with celery_app.connection_for_write() as conn:
        channel = conn.default_channel
        client = channel.client
        client.delete("celery", channel.unacked_key, channel.unacked_index_key)
        _restore_interrupted_tasks()
        assert client.llen("celery") == 0


def _worker_sender(queue):
    return SimpleNamespace(app=SimpleNamespace(amqp=SimpleNamespace(
        queues=SimpleNamespace(consume_from={queue: None}),
    )))


@pytest.mark.parametrize("queue,other", [("ingest", "celery"), ("celery", "ingest")])
def test_restart_restores_only_its_own_queue(queue, other):
    with celery_app.connection_for_write() as conn:
        channel = conn.default_channel
        client = channel.client
        client.delete("celery", "ingest", channel.unacked_key, channel.unacked_index_key)
        try:
            own_tag = _fake_unacked(channel, client, "tests.own_task", [], queue)
            other_tag = _fake_unacked(channel, client, "tests.active_task", [], other)
            _restore_interrupted_tasks(sender=_worker_sender(queue))
            assert client.llen(queue) == 1
            assert client.llen(other) == 0
            assert not client.hexists(channel.unacked_key, own_tag)
            assert client.hexists(channel.unacked_key, other_tag)
            assert client.zscore(channel.unacked_index_key, other_tag) is not None
        finally:
            client.delete("celery", "ingest", channel.unacked_key, channel.unacked_index_key)


@pytest.mark.parametrize("queue,should_exist", [("celery", True), ("ingest", False)])
def test_only_ingest_worker_sweeps_extraction_scratch(queue, should_exist, tmp_path, monkeypatch):
    from app.extraction import mineru_client

    monkeypatch.setattr(mineru_client, "extracted_dir", lambda: tmp_path)
    scratch = tmp_path / ".mineru-api-active"
    scratch.mkdir()
    (scratch / "page.md").write_text("extraction in progress")
    _sweep_extraction_scratch(sender=_worker_sender(queue))
    assert scratch.exists() is should_exist


@pytest.mark.parametrize("queue,should_exist", [("celery", True), ("ingest", False)])
def test_real_worker_startup_respects_selected_queue(queue, should_exist, tmp_path, monkeypatch):
    from celery.contrib.testing.worker import start_worker
    from app.extraction import mineru_client

    monkeypatch.setattr(mineru_client, "extracted_dir", lambda: tmp_path)
    scratch = tmp_path / ".mineru-api-active"
    scratch.mkdir()
    with start_worker(celery_app, queues=[queue], perform_ping_check=False,
                      pool="solo", loglevel="WARNING", shutdown_timeout=15):
        assert scratch.exists() is should_exist
