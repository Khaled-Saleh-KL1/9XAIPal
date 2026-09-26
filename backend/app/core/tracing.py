"""Pipeline tracing: OpenTelemetry spans exported to self-hosted Phoenix.

See docs/superpowers/specs/2026-09-26-pipeline-tracing-design.md. This is the
only module that imports OpenTelemetry. Every public function is a no-op when
TRACE_ENABLED is false or setup failed, and none of them can raise into the
caller: a broken recorder must never break a request or an ingestion.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import PurePath
from typing import Any, Callable, Iterator, Optional
from uuid import UUID

from app.core.config import settings

logger = logging.getLogger(__name__)

CHAIN = "CHAIN"
LLM = "LLM"
RETRIEVER = "RETRIEVER"
TOOL = "TOOL"
EMBEDDING = "EMBEDDING"

_MAX_ITEMS = 20
_CHUNK_TEXT_CHARS = 2000
_REDACT = ("authorization", "api_key", "apikey", "password", "secret", "token", "cookie")
_SKIP_ARGS = {"self", "cls", "session", "db", "request"}
_TRUNCATED = "…[truncated]"

_state: dict[str, Any] = {"pid": None, "tracer": None, "provider": None, "forced": None}
_lock = threading.Lock()
_last_warning = [0.0]


def _warn(message: str, exc: BaseException) -> None:
    now = time.monotonic()
    if now - _last_warning[0] >= 60:
        _last_warning[0] = now
        logger.warning("tracing: %s (%s: %s)", message, type(exc).__name__, exc)


# --- setup -------------------------------------------------------------------

def configure(exporter: Any = None) -> None:
    """Build this process's tracer. ``exporter=None`` exports to Phoenix."""
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "9xaipal"}))
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            headers = {"authorization": f"Bearer {settings.phoenix_api_key}"} if settings.phoenix_api_key else {}
            exporter = OTLPSpanExporter(settings.phoenix_collector_endpoint, headers=headers, timeout=5)
            provider.add_span_processor(BatchSpanProcessor(
                exporter, max_queue_size=2048, schedule_delay_millis=2000,
                max_export_batch_size=256, export_timeout_millis=5000,
            ))
        else:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        old = _state["provider"]
        _state.update(pid=os.getpid(), tracer=provider.get_tracer("app.core.tracing"), provider=provider)
        if old is not None and old is not provider:
            _shutdown_quietly(old)
    except Exception as exc:  # noqa: BLE001 — never raise into the app
        _warn("setup failed; tracing disabled for this process", exc)
        _state.update(pid=os.getpid(), tracer=None, provider=None)


def _shutdown_quietly(provider: Any) -> None:
    try:
        provider.shutdown()
    except Exception:  # noqa: BLE001
        pass


def shutdown(timeout_millis: int = 2000) -> None:
    provider = _state.get("provider")
    if provider is None:
        return
    try:
        provider.force_flush(timeout_millis)
    except Exception as exc:  # noqa: BLE001
        _warn("flush on shutdown failed", exc)


def use_in_memory_exporter():
    """Tests: record spans in memory regardless of TRACE_ENABLED."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    with _lock:
        configure(exporter)
        _state["forced"] = exporter
    return exporter


def reset_for_tests() -> None:
    with _lock:
        provider = _state.get("provider")
        _state.update(pid=None, tracer=None, provider=None, forced=None)
    if provider is not None:
        _shutdown_quietly(provider)


def _tracer():
    if _state["forced"] is None and not settings.trace_enabled:
        return None
    if _state["pid"] != os.getpid():  # first use, or a forked Celery child
        with _lock:
            if _state["pid"] != os.getpid():
                if _state["forced"] is not None:
                    configure(_state["forced"])
                else:
                    configure()
    return _state["tracer"]


# --- serialization --------------------------------------------------------

def _redacted(key: Any) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in _REDACT)


def jsonable(value: Any, _depth: int = 0) -> Any:
    """A bounded, secret-free, JSON-safe copy. Never iterates iterators."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (UUID, PurePath)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if _depth >= 6:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        items = list(value.items())
        out = {
            str(k): ("[redacted]" if _redacted(k) else jsonable(v, _depth + 1))
            for k, v in items[:_MAX_ITEMS]
        }
        if len(items) > _MAX_ITEMS:
            out["…"] = f"+{len(items) - _MAX_ITEMS} more"
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out = [jsonable(v, _depth + 1) for v in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            out.append(f"…(+{len(items) - _MAX_ITEMS} more)")
        return out
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return jsonable(value.model_dump(), _depth + 1)
        except Exception:  # noqa: BLE001
            return f"<{type(value).__name__}>"
    return f"<{type(value).__name__}>"


def _cap(text: str, limit: Optional[int] = None) -> str:
    limit = limit or settings.trace_max_text_chars
    return text if len(text) <= limit else text[:limit] + _TRUNCATED


def to_text(value: Any) -> str:
    if isinstance(value, str):
        return _cap(value)
    return _cap(json.dumps(jsonable(value), ensure_ascii=False, default=str))


def _attr_value(value: Any) -> Any:
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _cap(value)
    return to_text(value)


# --- spans ------------------------------------------------------------------

def _current():
    try:
        from opentelemetry import trace

        current = trace.get_current_span()
        return current if current.is_recording() else None
    except Exception:  # noqa: BLE001
        return None


def _set_on(otel_span: Any, values: dict[str, Any]) -> None:
    if otel_span is None:
        return
    for key, value in values.items():
        if value is None:
            continue
        try:
            otel_span.set_attribute(key, "[redacted]" if _redacted(key) else _attr_value(value))
        except Exception as exc:  # noqa: BLE001
            _warn(f"could not set attribute {key}", exc)


def _finish(otel_span: Any, status: str, exc: Optional[BaseException] = None) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode

        otel_span.set_attribute("status", status)
        if exc is not None and status == "error":
            otel_span.set_attribute("error.type", type(exc).__name__)
            otel_span.set_attribute("error.message", _cap(str(exc), 2000))
            otel_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
        elif status == "ok":
            otel_span.set_status(Status(StatusCode.OK))
    except Exception as err:  # noqa: BLE001
        _warn("could not set span status", err)


def _status_for(exc: BaseException) -> str:
    import asyncio

    return "cancelled" if isinstance(exc, (GeneratorExit, asyncio.CancelledError, KeyboardInterrupt)) else "error"


@contextmanager
def span(name: str, kind: str = CHAIN, *, context: Any = None, **attributes: Any) -> Iterator[Any]:
    """Record a block. Yields the OpenTelemetry span, or None when off."""
    tracer = None
    manager = None
    otel_span = None
    try:
        tracer = _tracer()
        if tracer is not None:
            manager = tracer.start_as_current_span(
                name, context=context, record_exception=False, set_status_on_exception=False,
            )
            otel_span = manager.__enter__()
            _set_on(otel_span, {"openinference.span.kind": kind, **attributes})
    except Exception as exc:  # noqa: BLE001
        _warn(f"could not start span {name}", exc)
        manager = otel_span = None
    if manager is None:
        yield None
        return
    try:
        yield otel_span
    except BaseException as exc:
        _finish(otel_span, _status_for(exc), exc)
        _exit_quietly(manager, exc)
        raise
    _finish(otel_span, "ok")
    _exit_quietly(manager, None)


def _exit_quietly(manager: Any, exc: Optional[BaseException]) -> None:
    try:
        if exc is None:
            manager.__exit__(None, None, None)
        else:
            manager.__exit__(type(exc), exc, exc.__traceback__)
    except Exception as err:  # noqa: BLE001
        _warn("could not end span", err)


def set_attributes(**values: Any) -> None:
    try:
        _set_on(_current(), values)
    except Exception as exc:  # noqa: BLE001
        _warn("set_attributes failed", exc)


def record_input(value: Any) -> None:
    try:
        current = _current()
        if current is None:
            return
        _set_on(current, {"input.value": _safe_text(value), "input.mime_type": _mime(value)})
    except Exception as exc:  # noqa: BLE001
        _warn("record_input failed", exc)


def record_output(value: Any) -> None:
    try:
        current = _current()
        if current is None:
            return
        _set_on(current, {"output.value": _safe_text(value), "output.mime_type": _mime(value)})
    except Exception as exc:  # noqa: BLE001
        _warn("record_output failed", exc)


def add_event(name: str, **values: Any) -> None:
    try:
        current = _current()
        if current is not None:
            current.add_event(name, {k: ("[redacted]" if _redacted(k) else _attr_value(v)) for k, v in values.items() if v is not None})
    except Exception as exc:  # noqa: BLE001
        _warn("add_event failed", exc)


def _mime(value: Any) -> str:
    return "text/plain" if isinstance(value, str) else "application/json"


def _safe_text(value: Any) -> Optional[str]:
    try:
        return to_text(value)
    except Exception as exc:  # noqa: BLE001
        _warn("could not serialize value", exc)
        return None


def _bound_args(fn: Callable, args: tuple, kwargs: dict) -> dict[str, Any]:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        return {"args": list(args), "kwargs": kwargs}
    return {k: v for k, v in bound.arguments.items() if k not in _SKIP_ARGS}


def _call_hook(hook: Optional[Callable], *args: Any, **kwargs: Any) -> Any:
    if hook is None:
        return None
    try:
        return hook(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _warn(f"hook {getattr(hook, '__name__', hook)} failed", exc)
        return None


def traced(
    name: str,
    kind: str = CHAIN,
    *,
    record_args: bool = True,
    input: Optional[Callable[..., Any]] = None,  # noqa: A002 — mirrors OpenInference naming
    output: Optional[Callable[[Any], Any]] = None,
    attributes: Optional[Callable[..., dict]] = None,
) -> Callable:
    """Decorate a function so each call is one block. Behavior is unchanged."""

    def decorate(fn: Callable) -> Callable:
        def start_values(args: tuple, kwargs: dict) -> dict[str, Any]:
            values = dict(_call_hook(attributes, *args, **kwargs) or {})
            if input is not None:
                values["input.value"] = _safe_text(_call_hook(input, *args, **kwargs))
            elif record_args:
                values["input.value"] = _safe_text(_bound_args(fn, args, kwargs))
            return values

        def end_values(result: Any) -> dict[str, Any]:
            shown = _call_hook(output, result) if output is not None else result
            return {"output.value": _safe_text(shown)}

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def agen_wrapper(*args, **kwargs):
                with span(name, kind, **(start_values(args, kwargs) if _tracer() is not None else {})) as current:
                    collected: list = []
                    async for item in fn(*args, **kwargs):
                        if current is not None and len(collected) < 100_000:
                            collected.append(item)
                        yield item
                    if current is not None:
                        _set_on(current, end_values(collected))
            wrapper = agen_wrapper
        elif inspect.isgeneratorfunction(fn):
            @functools.wraps(fn)
            def gen_wrapper(*args, **kwargs):
                with span(name, kind, **(start_values(args, kwargs) if _tracer() is not None else {})) as current:
                    collected: list = []
                    for item in fn(*args, **kwargs):
                        if current is not None and len(collected) < 100_000:
                            collected.append(item)
                        yield item
                    if current is not None:
                        _set_on(current, end_values(collected))
            wrapper = gen_wrapper
        elif inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                with span(name, kind, **(start_values(args, kwargs) if _tracer() is not None else {})) as current:
                    result = await fn(*args, **kwargs)
                    if current is not None:
                        _set_on(current, end_values(result))
                    return result
            wrapper = async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                with span(name, kind, **(start_values(args, kwargs) if _tracer() is not None else {})) as current:
                    result = fn(*args, **kwargs)
                    if current is not None:
                        _set_on(current, end_values(result))
                    return result
            wrapper = sync_wrapper
        wrapper.__traced__ = (name, kind)  # type: ignore[attr-defined]
        return wrapper

    return decorate


# --- OpenInference helpers --------------------------------------------------

def llm_messages_attributes(prefix: str, messages: list[dict]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    try:
        for i, message in enumerate(list(messages)[:_MAX_ITEMS]):
            attrs[f"{prefix}.{i}.message.role"] = str(message.get("role", ""))
            content = message.get("content", "")
            attrs[f"{prefix}.{i}.message.content"] = to_text(content if isinstance(content, str) else jsonable(content))
            if message.get("images"):
                attrs[f"{prefix}.{i}.message.images"] = f"<{len(message['images'])} image(s)>"
    except Exception as exc:  # noqa: BLE001
        _warn("could not render LLM messages", exc)
    return attrs


def retrieval_attributes(chunks: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    try:
        items = list(chunks)[:_MAX_ITEMS] if isinstance(chunks, (list, tuple)) else []
        for i, chunk in enumerate(items):
            if not isinstance(chunk, dict):
                continue
            prefix = f"retrieval.documents.{i}.document"
            attrs[f"{prefix}.id"] = str(chunk.get("id") or chunk.get("chunk_id") or chunk.get("sequence_id") or i)
            text = chunk.get("plain_text") or chunk.get("markdown") or chunk.get("content") or ""
            attrs[f"{prefix}.content"] = _cap(str(text), _CHUNK_TEXT_CHARS)
            score = chunk.get("score", chunk.get("similarity"))
            if isinstance(score, (int, float)):
                attrs[f"{prefix}.score"] = float(score)
            meta = {k: chunk.get(k) for k in ("document_id", "page_start", "page_end", "chunk_type", "heading_path") if chunk.get(k) is not None}
            if meta:
                attrs[f"{prefix}.metadata"] = to_text(meta)
    except Exception as exc:  # noqa: BLE001
        _warn("could not render retrieval documents", exc)
    return attrs


# --- context propagation ---------------------------------------------------

def current_context_carrier() -> dict[str, str]:
    carrier: dict[str, str] = {}
    try:
        if _tracer() is None:
            return carrier
        from opentelemetry.propagate import inject

        inject(carrier)
    except Exception as exc:  # noqa: BLE001
        _warn("could not inject context", exc)
    return carrier


def context_from_carrier(carrier: Any) -> Any:
    try:
        if _tracer() is None or not carrier:
            return None
        from opentelemetry.propagate import extract

        return extract(dict(carrier))
    except Exception as exc:  # noqa: BLE001
        _warn("could not extract context", exc)
        return None
