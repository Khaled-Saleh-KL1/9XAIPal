"""Celery tasks become children of the request that dispatched them."""

import os
import time
from types import SimpleNamespace

import pytest

from app.core import tracing, tracing_celery


@pytest.fixture
def spans():
    exporter = tracing.use_in_memory_exporter()
    yield exporter
    tracing.reset_for_tests()


def _task(name="app.workers.tasks.process_ingestion", headers=None):
    request = SimpleNamespace(headers=headers or {}, id="task-1")
    return SimpleNamespace(name=name, request=request)


def test_publish_inside_a_span_carries_the_trace(spans):
    headers = {}
    with tracing.span("POST /api/v1/papers/upload") as root:
        tracing_celery._inject(headers=headers)

    carried = headers[tracing_celery.TRACE_HEADER]
    assert carried["traceparent"].split("-")[1] == format(root.get_span_context().trace_id, "032x")
    assert abs(carried["published_at"] - time.time()) < 5


def test_task_span_is_a_child_and_records_queue_wait(spans):
    headers = {}
    with tracing.span("upload") as root:
        tracing_celery._inject(headers=headers)
    headers[tracing_celery.TRACE_HEADER]["published_at"] -= 2.5
    task = _task(headers=headers)

    doc = "5ffce224-80ab-5d5a-b53f-462e57021237"
    tracing_celery._start(task_id="task-1", task=task, args=(doc, "7c3a9a9e-0000-4000-8000-000000000001", "p.pdf"), kwargs={})
    tracing_celery._end(task_id="task-1", state="SUCCESS")

    task_span = [s for s in spans.get_finished_spans() if s.name == "task:process_ingestion"][0]
    assert task_span.context.trace_id == root.get_span_context().trace_id
    assert task_span.attributes["document.id"] == doc
    assert task_span.attributes["job.id"] == "7c3a9a9e-0000-4000-8000-000000000001"
    assert task_span.attributes["celery.queue_wait_ms"] >= 2400


def test_headers_nested_under_request_attributes_are_found(spans):
    headers = {}
    with tracing.span("upload"):
        tracing_celery._inject(headers=headers)
    request = SimpleNamespace(headers=None, id="t", **{tracing_celery.TRACE_HEADER: headers[tracing_celery.TRACE_HEADER]})
    tracing_celery._start(task_id="t", task=SimpleNamespace(name="x.embed_document", request=request), args=("d",), kwargs={})
    tracing_celery._end(task_id="t", state="SUCCESS")
    assert [s.name for s in spans.get_finished_spans()][-1] == "task:embed_document"


def test_failed_task_is_marked_error(spans):
    tracing_celery._start(task_id="t2", task=_task(), args=(), kwargs={})
    tracing_celery._fail(task_id="t2", exception=ValueError("pdf not found"))
    tracing_celery._end(task_id="t2", state="FAILURE")

    s = [s for s in spans.get_finished_spans() if s.name == "task:process_ingestion"][0]
    assert s.attributes["error.type"] == "ValueError"


def test_task_without_trace_header_starts_its_own_trace(spans):
    tracing_celery._start(task_id="t3", task=_task(headers={}), args=(), kwargs={})
    tracing_celery._end(task_id="t3", state="SUCCESS")
    assert [s for s in spans.get_finished_spans() if s.name == "task:process_ingestion"][0].parent is None


def test_disabled_tracing_adds_no_header(monkeypatch):
    tracing.reset_for_tests()
    monkeypatch.setattr(tracing.settings, "trace_enabled", False)
    headers = {}
    tracing_celery._inject(headers=headers)
    assert headers == {}


def test_tracer_is_rebuilt_after_fork(spans, monkeypatch):
    first = tracing._state["tracer"]
    real_pid = os.getpid()
    # tracing.os is the same singleton `os` module as this test's own `os`
    # import, so patching its getpid in terms of `os.getpid()` would call the
    # patched function recursively. Capture the real pid first instead.
    monkeypatch.setattr(tracing.os, "getpid", lambda: real_pid + 1)
    tracing_celery._after_fork()
    assert tracing._state["pid"] == real_pid + 1
    assert tracing._state["tracer"] is not first


def test_real_worker_receives_the_trace_header(spans):
    from celery.contrib.testing.worker import start_worker
    from app.core.celery_app import celery_app

    @celery_app.task(name="tests.trace_probe")
    def trace_probe(document_id):
        return document_id

    with start_worker(celery_app, perform_ping_check=False, pool="solo", loglevel="WARNING"):
        with tracing.span("POST /api/v1/papers/upload") as root:
            result = trace_probe.delay("5ffce224-80ab-5d5a-b53f-462e57021237")
        assert result.get(timeout=20) == "5ffce224-80ab-5d5a-b53f-462e57021237"

    probe = [s for s in spans.get_finished_spans() if s.name == "task:trace_probe"][0]
    assert probe.context.trace_id == root.get_span_context().trace_id
