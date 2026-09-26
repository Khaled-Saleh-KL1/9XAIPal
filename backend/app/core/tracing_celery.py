"""Celery signals: carry the trace from the dispatcher into each task."""

from __future__ import annotations

import os
import time
from typing import Any
from uuid import UUID

from celery.signals import (
    before_task_publish,
    task_failure,
    task_postrun,
    task_prerun,
    task_revoked,
    worker_process_init,
)

from app.core import tracing

TRACE_HEADER = "x-9xaipal-trace"
_open: dict[str, tuple[Any, Any]] = {}


@before_task_publish.connect
def _inject(headers: dict | None = None, **_: Any) -> None:
    try:
        if headers is None:
            return
        carrier = tracing.current_context_carrier()
        if carrier:
            headers[TRACE_HEADER] = {**carrier, "published_at": time.time()}
    except Exception as exc:  # noqa: BLE001
        tracing._warn("could not inject task trace header", exc)


def _carried(request: Any) -> dict:
    for source in (getattr(request, "headers", None) or {}, getattr(request, "__dict__", {})):
        value = source.get(TRACE_HEADER) if isinstance(source, dict) else None
        if isinstance(value, dict):
            return value
    getter = getattr(request, "get", None)
    if callable(getter):
        value = getter(TRACE_HEADER)
        if isinstance(value, dict):
            return value
    return {}


def _uuid_or_none(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


@task_prerun.connect
def _start(task_id: str | None = None, task: Any = None, args: tuple = (), kwargs: dict | None = None, **_: Any) -> None:
    try:
        if task_id is None or task is None or tracing._tracer() is None:
            return
        carried = _carried(getattr(task, "request", None))
        context = tracing.context_from_carrier({k: v for k, v in carried.items() if k != "published_at"})
        short = str(getattr(task, "name", "task")).rsplit(".", 1)[-1]
        attributes: dict[str, Any] = {"celery.task_id": task_id, "celery.task_name": getattr(task, "name", None)}
        published = carried.get("published_at")
        if isinstance(published, (int, float)):
            attributes["celery.queue_wait_ms"] = max(0, int((time.time() - published) * 1000))
        args = tuple(args or ())
        if args:
            attributes["document.id"] = _uuid_or_none(args[0])
        if short == "process_ingestion" and len(args) > 1:
            attributes["job.id"] = _uuid_or_none(args[1])
        manager = tracing.span(f"task:{short}", tracing.CHAIN, context=context, **attributes)
        manager.__enter__()
        _open[task_id] = (manager, None)
    except Exception as exc:  # noqa: BLE001
        tracing._warn("could not start task span", exc)


@task_failure.connect
def _fail(task_id: str | None = None, exception: BaseException | None = None, **_: Any) -> None:
    try:
        if task_id in _open and exception is not None:
            manager, _ = _open[task_id]
            _open[task_id] = (manager, exception)
    except Exception as exc:  # noqa: BLE001
        tracing._warn("could not record task failure", exc)


@task_postrun.connect
def _end(task_id: str | None = None, state: str | None = None, **_: Any) -> None:
    entry = _open.pop(task_id, None) if task_id else None
    if entry is None:
        return
    manager, exception = entry
    try:
        tracing.set_attributes(**{"celery.state": state})
        if exception is not None:
            manager.__exit__(type(exception), exception, exception.__traceback__)
        else:
            manager.__exit__(None, None, None)
    except Exception as exc:  # noqa: BLE001 — never raise out of a Celery signal
        tracing._warn("could not end task span", exc)


@task_revoked.connect
def _revoked(request: Any = None, **_: Any) -> None:
    """A revoked/terminated task fires this instead of ``task_postrun``.

    Close its span here too, or it (and its ``_open`` entry) leaks forever.
    Exiting with a ``KeyboardInterrupt`` instance makes ``tracing.span``'s
    ``_status_for`` classify it as "cancelled" rather than "error", and
    ``_GeneratorContextManager.__exit__`` swallows it (it re-raises only if
    the exception it gets back differs from the one it was given).
    """
    try:
        task_id = getattr(request, "id", None)
        entry = _open.pop(task_id, None) if task_id else None
        if entry is None:
            return
        manager, _ = entry
        manager.__exit__(KeyboardInterrupt, KeyboardInterrupt(), None)
    except Exception as exc:  # noqa: BLE001
        tracing._warn("could not end revoked task span", exc)


@worker_process_init.connect
def _after_fork(**_: Any) -> None:
    """A prefork child must not reuse the parent's exporter thread.

    Guarded on the pid actually changing: some execution pools (e.g. the
    embedded ``solo`` worker used in tests, via ``celery.contrib.testing``)
    fire this signal without an actual ``fork()``. Rebuilding unconditionally
    there would call ``tracing.configure()`` again on the *same* process,
    which shuts down the still-live previous ``TracerProvider`` — and since a
    ``SimpleSpanProcessor.shutdown()`` also shuts down its ``span_exporter``,
    a shared (forced/test) exporter would be marked stopped and silently
    drop every span recorded afterwards. A real fork never hits this: the
    child's "old" provider is its own copy in a separate OS process, so
    shutting it down there never touches the parent's live one.
    """
    try:
        if tracing._state.get("pid") == os.getpid():
            return
        tracing._state["pid"] = None
        tracing._tracer()
    except Exception as exc:  # noqa: BLE001
        tracing._warn("could not rebuild tracer after fork", exc)
