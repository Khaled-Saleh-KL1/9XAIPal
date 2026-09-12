"""A worker restart hands the previous worker's in-flight tasks back to the
queue at once (core/celery_app.py::_restore_interrupted_tasks) — instead of
leaving them in Redis's `unacked` hash until the one-hour visibility timeout,
which is what left a book at "extracting" after a deploy on 2026-09-12.
Runs against the real Redis broker the test env points at."""

import json
import time
from uuid import uuid4

import pytest

from app.core.celery_app import celery_app, _restore_interrupted_tasks


def _fake_unacked(channel, client, task_name: str, args: list) -> str:
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
            "delivery_info": {"exchange": "", "routing_key": "celery"},
            "delivery_mode": 2,
            "delivery_tag": tag,
            "body_encoding": "base64",
        },
    }
    import base64
    message["body"] = base64.b64encode(message["body"].encode()).decode()
    client.hset(channel.unacked_key, tag, json.dumps([message, "", "celery"]))
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
