# Pipeline Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every 9XAIPal chat question and ingestion as a tree of OpenTelemetry spans and show them in a self-hosted Phoenix at `https://9xaipal-trace.kl1.site`, without changing anything the app computes.

**Architecture:** One module, `app/core/tracing.py`, owns OpenTelemetry. It exposes a `span()` context manager and a `traced()` decorator (sync, async, generators). Both are no-ops when tracing is off or broken. Spans reach the app through three routes: an ASGI middleware (one root span per mutating HTTP request), Celery signals (one span per task, joined to the dispatching request's trace), and decorators on existing functions. Spans are batched to Phoenix over OTLP/HTTP; Phoenix stores them in its own database on the existing Postgres.

**Tech Stack:** Python 3.12, FastAPI 0.141 / Starlette 0.52, Celery 5.6.3, `opentelemetry-sdk` 1.45.0 and `opentelemetry-exporter-otlp-proto-http` 1.45.0, OpenInference attribute conventions (strings only, no extra package), Arize Phoenix `arizephoenix/phoenix:version-20.16.0-nonroot`, host nginx + certbot.

**Spec:** `docs/superpowers/specs/2026-09-26-pipeline-tracing-design.md`

## Global Constraints

- Tracing must not change any value the app computes, stores, returns or dispatches. Wrappers never mutate arguments, never consume iterators or generators they did not create, and re-raise every exception unchanged.
- No tracing call may raise into the caller. Recorder errors are logged at most once per minute (`tracing: …`) and dropped.
- `TRACE_ENABLED` defaults to `false`. With it off, no OpenTelemetry object is created and every wrapper returns immediately.
- `TRACE_MAX_TEXT_CHARS=8000`; retrieved chunk text capped at 2,000 characters; arrays keep the first 20 items plus a count; image bytes are never recorded.
- Redacted keys (case-insensitive substring match): `authorization`, `api_key`, `apikey`, `password`, `secret`, `token`, `cookie` → value `[redacted]`.
- Span kinds (attribute `openinference.span.kind`): `CHAIN`, `LLM`, `RETRIEVER`, `TOOL`, `EMBEDDING`.
- `uv.lock`: adding the OpenTelemetry packages must change **no** existing package version (verified by the trial resolution on 2026-09-26: 9 packages added at 1.45.0 / 0.66b0, nothing changed or removed).
- Phoenix image pinned to `arizephoenix/phoenix:version-20.16.0-nonroot`; memory limit 512 MB; 1 CPU; retention `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=14`; published only on `127.0.0.1:6006`.
- English isolation proof: the main-vs-branch differential harness (15 recorded MinerU documents) must show identical persisted output with tracing off **and** on.
- Test command (runner container, from `backend/`): `DEBUG=true POSTGRES_DB=9xaipal_test POSTGRES_HOST=host.docker.internal POSTGRES_PORT=55439 REDIS_URL=redis://host.docker.internal:55440/0 PYTHONPATH=. python -m pytest …`. Abbreviated below as `$PYTEST`.

## Review Focus

1. **Generators passed through a traced function** (e.g. an async iterator argument, or `stream_chat` output): the wrapper must forward every event unchanged, in order, and never iterate an argument. Pinned in Task 1 (`test_traced_async_generator_forwards_events_unchanged`, `test_arguments_that_are_iterators_are_not_consumed`).
2. **Client disconnect mid-stream** (`GeneratorExit` / `asyncio.CancelledError` thrown into a traced async generator): the span must end with status `cancelled`, and the exception must propagate. Pinned in Task 1 (`test_traced_async_generator_close_marks_cancelled`).
3. **Celery prefork after the parent created a tracer:** the child process must build its own exporter, not reuse the parent's dead batch thread. Pinned in Task 3 (`test_tracer_is_rebuilt_after_fork`).
4. **Secrets inside nested structures** (an `Authorization` header in a dict inside a list, a `*_API_KEY` kwarg): never appear in any exported attribute. Pinned in Task 1 (`test_redaction_reaches_nested_values`).
5. **Phoenix down or slow:** requests must not wait. The exporter uses a bounded queue and a short export timeout. Pinned in Task 1 (`test_unreachable_collector_never_blocks_or_raises`).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `backend/app/core/tracing.py` (new) | Everything OpenTelemetry: setup per process, `span`, `traced`, attribute helpers, serialization, redaction, test hook |
| `backend/app/core/tracing_http.py` (new) | ASGI middleware: one root span per non-GET API request |
| `backend/app/core/tracing_celery.py` (new) | Celery signal handlers: inject trace context at publish, one span per task |
| `backend/app/core/config.py` | `trace_enabled`, `trace_max_text_chars`, `phoenix_collector_endpoint`, `phoenix_api_key` |
| `backend/app/main.py` | install `TraceRequestMiddleware` |
| `backend/app/core/celery_app.py` | import `app.core.tracing_celery` so its signals connect |
| `backend/app/api/deps.py` | tag the current span with `user.id` |
| `backend/app/llm/client.py` | `llm.chat` spans on each provider attempt |
| `backend/app/chat/*.py`, `services/retrieval.py`, `search/web.py` | decorators on existing functions |
| `backend/app/extraction/*.py`, `embeddings/service_sync.py`, `summarization/*.py` | decorators on existing functions |
| `backend/tests/test_tracing_*.py` (new) | tests per task |
| `backend/docker-compose.prod.yml`, `backend/docker-compose.yml` | `phoenix` service; tracing env for api/worker |
| `backend/.env.example` | new variables |
| `backend/nginx/9xaipal-trace.conf` (new) | public HTTPS site for Phoenix |
| `scripts/deploy-once.sh` | ensure the `phoenix` database and service on every deploy |
| `docs/runbooks/pipeline-tracing.md` (new) | operations: enable, disable, rotate key, retention |

---

### Task 1: Recorder core (`app/core/tracing.py`)

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`
- Modify: `backend/app/core/config.py` (after the Arabic settings block, before `settings = Settings()`)
- Create: `backend/app/core/tracing.py`
- Test: `backend/tests/test_tracing_recorder.py`

**Interfaces:**
- Produces:
  - Constants `CHAIN, LLM, RETRIEVER, TOOL, EMBEDDING: str`
  - `span(name: str, kind: str = CHAIN, **attributes) -> ContextManager[Span | None]`
  - `traced(name: str, kind: str = CHAIN, *, record_args: bool = True, input: Callable | None = None, output: Callable | None = None, attributes: Callable | None = None)` — decorator for sync functions, coroutines, sync generators and async generators. `input(*args, **kwargs) -> Any`, `output(result) -> Any`, `attributes(*args, **kwargs) -> dict`.
  - `set_attributes(**values) -> None`, `record_input(value) -> None`, `record_output(value) -> None`, `add_event(name: str, **values) -> None` — act on the current span.
  - `llm_messages_attributes(prefix: str, messages: list[dict]) -> dict[str, str]`
  - `retrieval_attributes(chunks: list[dict]) -> dict[str, Any]`
  - `current_context_carrier() -> dict[str, str]`, `context_from_carrier(carrier: dict) -> Context | None`
  - `configure(exporter=None) -> None`, `use_in_memory_exporter() -> InMemorySpanExporter` (tests), `reset_for_tests() -> None`
  - `jsonable(value) -> Any`, `to_text(value) -> str`

- [ ] **Step 1: Add the dependencies without changing any existing version**

```bash
cd backend
cp uv.lock /tmp/uv.lock.before
uv add --no-sync opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
python3 - <<'EOF'
import tomllib
a={p["name"]:p.get("version") for p in tomllib.load(open("/tmp/uv.lock.before","rb"))["package"]}
b={p["name"]:p.get("version") for p in tomllib.load(open("uv.lock","rb"))["package"]}
changed={n:(a[n],b[n]) for n in a if n in b and a[n]!=b[n]}
removed=sorted(set(a)-set(b))
print("changed:",changed,"removed:",removed)
assert not changed and not removed, "an existing dependency changed — stop and report"
EOF
```

Expected: `changed: {} removed: []`. If not empty, stop and report to the owner; do not continue.

Then pin the lower bounds in `pyproject.toml` (next to the `google-genai` entry):

```toml
    # Pipeline tracing (TRACE_ENABLED; exported to self-hosted Phoenix).
    "opentelemetry-sdk>=1.45.0",
    "opentelemetry-exporter-otlp-proto-http>=1.45.0",
```

Re-run `uv lock` and the same check script.

- [ ] **Step 2: Add the settings**

In `app/core/config.py`, before `settings = Settings()` (inside `class Settings`, after the Arabic OCR fields):

```python
    # Pipeline tracing (docs/superpowers/specs/2026-09-26-pipeline-tracing-design.md).
    # Off by default: with it off no OpenTelemetry object is ever created.
    trace_enabled: bool = False
    trace_max_text_chars: int = Field(default=8000, ge=200, le=200_000)
    phoenix_collector_endpoint: str = "http://phoenix:6006/v1/traces"
    phoenix_api_key: str = ""
```

- [ ] **Step 3: Write the failing recorder tests**

Create `backend/tests/test_tracing_recorder.py`:

```python
"""The recorder never changes what the traced code does."""

import asyncio
import time

import pytest

from app.core import tracing


@pytest.fixture
def spans():
    exporter = tracing.use_in_memory_exporter()
    yield exporter
    tracing.reset_for_tests()


def _by_name(exporter, name):
    found = [s for s in exporter.get_finished_spans() if s.name == name]
    assert found, f"no span {name!r}; got {[s.name for s in exporter.get_finished_spans()]}"
    return found[0]


def test_disabled_tracing_creates_nothing_and_returns_values_unchanged(monkeypatch):
    tracing.reset_for_tests()
    monkeypatch.setattr(tracing.settings, "trace_enabled", False)

    @tracing.traced("double")
    def double(x):
        return x * 2

    with tracing.span("outer") as current:
        assert current is None
        assert double(4) == 8
    assert tracing._state["tracer"] is None


def test_sync_span_records_kind_input_output(spans):
    @tracing.traced("add", kind=tracing.TOOL)
    def add(a, b):
        return {"sum": a + b}

    assert add(2, b=3) == {"sum": 5}

    s = _by_name(spans, "add")
    assert s.attributes["openinference.span.kind"] == "TOOL"
    assert '"a": 2' in s.attributes["input.value"]
    assert '"sum": 5' in s.attributes["output.value"]
    assert s.status.is_ok


async def _async_add(a, b):
    await asyncio.sleep(0)
    return a + b


def test_async_function_is_traced(spans):
    traced = tracing.traced("async_add")(_async_add)

    assert asyncio.run(traced(1, 2)) == 3
    assert _by_name(spans, "async_add").attributes["output.value"] == "3"


def test_nested_spans_share_one_trace(spans):
    @tracing.traced("inner")
    def inner():
        return 1

    with tracing.span("outer"):
        inner()

    outer, child = _by_name(spans, "outer"), _by_name(spans, "inner")
    assert child.context.trace_id == outer.context.trace_id
    assert child.parent.span_id == outer.context.span_id


def test_exception_propagates_unchanged_and_marks_error(spans):
    class Boom(ValueError):
        pass

    @tracing.traced("explode")
    def explode():
        raise Boom("bad input")

    with pytest.raises(Boom, match="bad input"):
        explode()

    s = _by_name(spans, "explode")
    assert not s.status.is_ok
    assert s.attributes["error.type"] == "Boom"
    assert "bad input" in s.attributes["error.message"]


def test_traced_async_generator_forwards_events_unchanged(spans):
    events = [{"type": "token", "text": "Hel"}, {"type": "token", "text": "lo"}, {"type": "done", "content": "Hello"}]

    async def stream():
        for event in events:
            yield event

    traced = tracing.traced("stream", output=lambda collected: "".join(e.get("text", "") for e in collected))(stream)

    async def consume():
        return [event async for event in traced()]

    received = asyncio.run(consume())
    assert received == events
    assert all(a is b for a, b in zip(received, events))
    assert _by_name(spans, "stream").attributes["output.value"] == "Hello"


def test_traced_async_generator_close_marks_cancelled(spans):
    async def stream():
        yield 1
        yield 2

    traced = tracing.traced("cut")(stream)

    async def consume_one_then_close():
        agen = traced()
        assert await agen.__anext__() == 1
        await agen.aclose()

    asyncio.run(consume_one_then_close())
    s = _by_name(spans, "cut")
    assert s.attributes["status"] == "cancelled"


def test_arguments_that_are_iterators_are_not_consumed(spans):
    @tracing.traced("take")
    def take(items):
        return list(items)

    assert take(iter([1, 2, 3])) == [1, 2, 3]
    assert "list_iterator" in _by_name(spans, "take").attributes["input.value"]


def test_long_text_is_truncated_and_marked(spans, monkeypatch):
    monkeypatch.setattr(tracing.settings, "trace_max_text_chars", 500)

    @tracing.traced("echo")
    def echo(text):
        return text

    echo("x" * 5000)
    value = _by_name(spans, "echo").attributes["output.value"]
    assert len(value) <= 520
    assert value.endswith("…[truncated]")


def test_arrays_keep_first_twenty_items_and_a_count():
    rendered = tracing.jsonable(list(range(50)))

    assert rendered[:20] == list(range(20))
    assert rendered[20] == "…(+30 more)"


def test_redaction_reaches_nested_values(spans):
    @tracing.traced("call_provider")
    def call_provider(payload, gemini_api_key=None):
        return {"headers": {"Authorization": "Bearer sk-live-123"}, "ok": True}

    call_provider([{"headers": {"authorization": "Bearer abc"}, "password": "p"}], gemini_api_key="AIza-secret")

    exported = " ".join(str(v) for s in spans.get_finished_spans() for v in s.attributes.values())
    for secret in ("sk-live-123", "Bearer abc", "AIza-secret", '"p"'):
        assert secret not in exported
    assert "[redacted]" in exported


def test_bytes_and_images_are_never_recorded(spans):
    @tracing.traced("image")
    def image(png):
        return png

    image(b"\x89PNG" + b"0" * 10_000)
    value = _by_name(spans, "image").attributes["input.value"]
    assert "<10004 bytes>" in value
    assert "0000" not in value


def test_recorder_failure_never_fails_the_caller(spans, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("serializer broke")

    monkeypatch.setattr(tracing, "to_text", broken)

    @tracing.traced("still_works")
    def still_works(x):
        return x + 1

    assert still_works(1) == 2


def test_unreachable_collector_never_blocks_or_raises(monkeypatch):
    tracing.reset_for_tests()
    monkeypatch.setattr(tracing.settings, "trace_enabled", True)
    monkeypatch.setattr(tracing.settings, "phoenix_collector_endpoint", "http://127.0.0.1:9/v1/traces")
    tracing.configure()

    started = time.monotonic()
    for _ in range(50):
        with tracing.span("fire"):
            pass
    assert time.monotonic() - started < 1.0
    tracing.reset_for_tests()


def test_context_carrier_round_trip(spans):
    with tracing.span("parent") as parent:
        carrier = tracing.current_context_carrier()
    assert "traceparent" in carrier

    with tracing.span("child", context=tracing.context_from_carrier(carrier)):
        pass
    child = _by_name(spans, "child")
    assert child.context.trace_id == parent.get_span_context().trace_id


def test_llm_messages_attributes_follow_openinference():
    attrs = tracing.llm_messages_attributes(
        "llm.input_messages", [{"role": "system", "content": "s"}, {"role": "user", "content": "q", "images": ["b64"]}]
    )

    assert attrs["llm.input_messages.0.message.role"] == "system"
    assert attrs["llm.input_messages.1.message.content"] == "q"
    assert "b64" not in " ".join(attrs.values())


def test_retrieval_attributes_cap_chunk_text():
    attrs = tracing.retrieval_attributes([{"id": "c1", "plain_text": "y" * 5000, "score": 0.9, "page_start": 3}])

    assert attrs["retrieval.documents.0.document.id"] == "c1"
    assert len(attrs["retrieval.documents.0.document.content"]) <= 2020
    assert attrs["retrieval.documents.0.document.score"] == 0.9
```

- [ ] **Step 4: Run the tests and watch them fail**

Run: `$PYTEST tests/test_tracing_recorder.py -q`
Expected: collection error `ImportError: cannot import name 'tracing'`.

- [ ] **Step 5: Implement `app/core/tracing.py`**

```python
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
    set_attributes(**{"input.value": _safe_text(value), "input.mime_type": _mime(value)})


def record_output(value: Any) -> None:
    set_attributes(**{"output.value": _safe_text(value), "output.mime_type": _mime(value)})


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
```

The `if _tracer() is not None` guard keeps the disabled path free of any serialization work.

Add the helpers:

```python
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
```

- [ ] **Step 6: Run the tests until they pass**

Run: `$PYTEST tests/test_tracing_recorder.py -q`
Expected: all pass. If `test_unreachable_collector_never_blocks_or_raises` is slow, the exporter is exporting inline — it must use `BatchSpanProcessor`.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/core/config.py backend/app/core/tracing.py backend/tests/test_tracing_recorder.py
git commit -m "feat: add fail-safe OpenTelemetry recorder for pipeline tracing"
```

---

### Task 2: Root span per API request, tagged with the user

**Files:**
- Create: `backend/app/core/tracing_http.py`
- Modify: `backend/app/main.py:23-35` (middleware registration), `backend/app/api/deps.py:53-80` (`get_current_user`)
- Test: `backend/tests/test_tracing_http.py`

**Interfaces:**
- Consumes: `tracing.span`, `tracing.set_attributes`, `tracing.CHAIN`
- Produces: `TraceRequestMiddleware(app)` — pure ASGI; root span named `"{METHOD} {path template}"` for every non-GET/HEAD/OPTIONS request under `/api/`, with `http.method`, `http.route`, `http.status_code`; the span stays open until the response body (including a stream) is fully sent.

- [ ] **Step 1: Write the failing tests**

```python
"""One root span per mutating API request; reads are not traced."""

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
import pytest

from app.core import tracing
from app.core.tracing_http import TraceRequestMiddleware


@pytest.fixture
def spans():
    exporter = tracing.use_in_memory_exporter()
    yield exporter
    tracing.reset_for_tests()


def _app():
    app = FastAPI()
    app.add_middleware(TraceRequestMiddleware)

    @app.get("/api/v1/papers/{paper_id}/progress")
    async def progress(paper_id: str):
        return {"id": paper_id}

    @app.post("/api/v1/ask")
    async def ask():
        with tracing.span("inner"):
            return {"answer": "ok"}

    @app.post("/api/v1/ask/stream")
    async def ask_stream():
        async def body():
            with tracing.span("streaming_inner"):
                yield b"a"
                yield b"b"
        return StreamingResponse(body())

    @app.post("/api/v1/fail")
    async def fail():
        raise RuntimeError("boom")

    return app


def test_get_requests_are_not_traced(spans):
    TestClient(_app()).get("/api/v1/papers/123/progress")
    assert spans.get_finished_spans() == ()


def test_post_request_is_the_root_of_its_trace(spans):
    response = TestClient(_app()).post("/api/v1/ask")

    assert response.json() == {"answer": "ok"}
    names = {s.name: s for s in spans.get_finished_spans()}
    root = names["POST /api/v1/ask"]
    assert root.parent is None
    assert names["inner"].parent.span_id == root.context.span_id
    assert root.attributes["http.status_code"] == 200


def test_streaming_response_is_inside_the_request_span(spans):
    response = TestClient(_app()).post("/api/v1/ask/stream")

    assert response.content == b"ab"
    names = {s.name: s for s in spans.get_finished_spans()}
    assert names["streaming_inner"].context.trace_id == names["POST /api/v1/ask/stream"].context.trace_id


def test_unhandled_error_still_propagates_and_is_recorded(spans):
    with pytest.raises(RuntimeError):
        TestClient(_app()).post("/api/v1/fail")
    root = [s for s in spans.get_finished_spans() if s.name == "POST /api/v1/fail"][0]
    assert root.attributes["error.type"] == "RuntimeError"


def test_disabled_tracing_leaves_responses_identical(monkeypatch):
    tracing.reset_for_tests()
    monkeypatch.setattr(tracing.settings, "trace_enabled", False)
    client = TestClient(_app())
    assert client.post("/api/v1/ask").json() == {"answer": "ok"}
    assert client.post("/api/v1/ask/stream").content == b"ab"


def test_user_id_is_attached_by_the_auth_dependency(spans, monkeypatch):
    from app.api import deps

    user = {"id": "11111111-1111-1111-1111-111111111111", "email": "x@example.test"}
    with tracing.span("request"):
        deps._trace_user(user)
    request_span = [s for s in spans.get_finished_spans() if s.name == "request"][0]
    assert request_span.attributes["user.id"] == user["id"]
    assert "email" not in " ".join(request_span.attributes.keys())
```

- [ ] **Step 2: Run to verify failure**

Run: `$PYTEST tests/test_tracing_http.py -q`
Expected: `ModuleNotFoundError: No module named 'app.core.tracing_http'`.

- [ ] **Step 3: Implement the middleware**

```python
"""ASGI middleware: one root span per mutating API request."""

from __future__ import annotations

from typing import Any

from app.core import tracing

_UNTRACED_METHODS = {"GET", "HEAD", "OPTIONS"}


class TraceRequestMiddleware:
    """Pure ASGI so a streamed response stays inside its request span."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method", "GET").upper() in _UNTRACED_METHODS
            or not str(scope.get("path", "")).startswith("/api/")
            or tracing._tracer() is None
        ):
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        path = scope.get("path", "")
        status: dict[str, int] = {}

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 0))
            await send(message)

        with tracing.span(f"{method} {path}", tracing.CHAIN, **{"http.method": method, "http.target": path}):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                route = scope.get("route")
                template = getattr(route, "path", None)
                tracing.set_attributes(**{
                    "http.status_code": status.get("code"),
                    "http.route": template,
                })
```

The span name uses the raw path so an id-bearing route stays readable (`POST /api/v1/papers/<id>/reextract`); `http.route` carries the template for grouping.

- [ ] **Step 4: Register it and tag the user**

`app/main.py`, after the `CORSMiddleware` block (so it is the outermost layer and times the whole request):

```python
from app.core.tracing_http import TraceRequestMiddleware

# Outermost: one root span per mutating API request (no-op unless TRACE_ENABLED).
app.add_middleware(TraceRequestMiddleware)
```

`app/api/deps.py`: add the helper and call it at the end of `get_current_user`, right before its `return user` (use the variable name the function already returns):

```python
from app.core import tracing


def _trace_user(user: dict) -> None:
    """Tag the current request's trace with who made it (id only)."""
    tracing.set_attributes(**{"user.id": str(user.get("id"))})
```

```python
    _trace_user(user)
    return user
```

- [ ] **Step 5: Run to verify pass, plus the auth tests**

Run: `$PYTEST tests/test_tracing_http.py tests/test_auth*.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/tracing_http.py backend/app/main.py backend/app/api/deps.py backend/tests/test_tracing_http.py
git commit -m "feat: trace each mutating API request as a root span tagged with the user"
```

---

### Task 3: Celery tasks join the dispatching trace

**Files:**
- Create: `backend/app/core/tracing_celery.py`
- Modify: `backend/app/core/celery_app.py` (import the module after `celery_app` is created)
- Test: `backend/tests/test_tracing_celery.py`

**Interfaces:**
- Consumes: `tracing.current_context_carrier()`, `tracing.context_from_carrier()`, `tracing.span`, `tracing.configure`
- Produces: signal handlers `_inject(headers, **_)`, `_start(task_id, task, args, kwargs, **_)`, `_end(task_id, state, **_)`, `_fail(task_id, exception, **_)`, `_after_fork(**_)`; header name `TRACE_HEADER = "x-9xaipal-trace"` holding `{"traceparent": ..., "published_at": <epoch seconds>}`. Each task span is named `task:<short task name>` with attributes `celery.task_id`, `celery.queue_wait_ms`, `document.id` (from the first positional argument when it parses as a UUID), `job.id` (second argument of `process_ingestion`).

- [ ] **Step 1: Write the failing tests**

```python
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
    monkeypatch.setattr(tracing.os, "getpid", lambda: os.getpid() + 1)
    tracing_celery._after_fork()
    assert tracing._state["pid"] == os.getpid() + 1
    assert tracing._state["tracer"] is not first
```

- [ ] **Step 2: Run to verify failure**

Run: `$PYTEST tests/test_tracing_celery.py -q`
Expected: `ImportError` for `tracing_celery`.

- [ ] **Step 3: Implement the signal handlers**

```python
"""Celery signals: carry the trace from the dispatcher into each task."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from celery.signals import before_task_publish, task_failure, task_postrun, task_prerun, worker_process_init

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
    if task_id in _open and exception is not None:
        manager, _ = _open[task_id]
        _open[task_id] = (manager, exception)


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


@worker_process_init.connect
def _after_fork(**_: Any) -> None:
    """A prefork child must not reuse the parent's exporter thread."""
    try:
        tracing._state["pid"] = None
        tracing._tracer()
    except Exception as exc:  # noqa: BLE001
        tracing._warn("could not rebuild tracer after fork", exc)
```

`span()` is a generator-based context manager: `__exit__` with the task's exception records it and returns `False` without raising, so the task's own failure handling is untouched.

`app/core/celery_app.py`, at the end of the file:

```python
# Trace propagation into tasks (no-op unless TRACE_ENABLED).
import app.core.tracing_celery  # noqa: E402,F401
```

- [ ] **Step 4: Run to verify pass**

Run: `$PYTEST tests/test_tracing_celery.py -q`
Expected: all pass.

- [ ] **Step 5: End-to-end check with a real worker and the test Redis**

Add to `tests/test_tracing_celery.py`:

```python
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
```

Run: `$PYTEST tests/test_tracing_celery.py -q`
Expected: pass. If the header is not found, inspect `task.request.__dict__` inside `_start` and extend `_carried` to that location; do not change the dispatch call sites.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/tracing_celery.py backend/app/core/celery_app.py backend/tests/test_tracing_celery.py
git commit -m "feat: join Celery tasks to the trace of the request that dispatched them"
```

---

### Task 4: LLM calls

**Files:**
- Modify: `backend/app/llm/client.py` (`_chat_once` ~line 170, `_stream_once` ~line 261, `_chat_sync_once` ~line 405)
- Test: `backend/tests/test_tracing_llm.py`

**Interfaces:**
- Consumes: `tracing.traced`, `tracing.LLM`, `tracing.llm_messages_attributes`
- Produces: one `llm.chat` span per provider attempt (a cascade fall-through shows as an error block followed by the next attempt), with `llm.model_name`, `llm.provider`, `llm.input_messages.*`, `llm.output_messages.0.message.*`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.finish_reason`.

- [ ] **Step 1: Write the failing tests**

```python
from types import SimpleNamespace
import asyncio

import pytest

from app.core import tracing
from app.llm import client


@pytest.fixture
def spans():
    exporter = tracing.use_in_memory_exporter()
    yield exporter
    tracing.reset_for_tests()


def _target(provider="nvidia"):
    return SimpleNamespace(provider=provider, api_key="nvapi-secret", base_url="https://x", key_index=0, breaker_id="b")


def test_attempt_functions_are_traced_as_llm():
    for fn in (client._chat_once, client._stream_once, client._chat_sync_once):
        assert fn.__traced__ == ("llm.chat", "LLM")


def test_sync_attempt_records_model_messages_answer_and_tokens(spans, monkeypatch):
    monkeypatch.setattr(client.ollama_client, "chat_sync", lambda messages, **kw: {
        "content": "answer", "model": "gemma", "prompt_tokens": 12, "completion_tokens": 3,
    })

    result = client._chat_sync_once(
        _target("ollama"), [{"role": "user", "content": "q?"}], resolved="gemma", temperature=0.3, images=None,
    )

    assert result["content"] == "answer"
    s = [s for s in spans.get_finished_spans() if s.name == "llm.chat"][0]
    assert s.attributes["llm.model_name"] == "gemma"
    assert s.attributes["llm.provider"] == "ollama"
    assert s.attributes["llm.input_messages.0.message.content"] == "q?"
    assert s.attributes["llm.output_messages.0.message.content"] == "answer"
    assert s.attributes["llm.token_count.prompt"] == 12
    assert "nvapi-secret" not in " ".join(str(v) for v in s.attributes.values())


def test_stream_attempt_forwards_every_event_and_records_the_answer(spans, monkeypatch):
    async def fake_stream(target, messages, **kwargs):
        yield {"type": "token", "text": "He"}
        yield {"type": "token", "text": "llo"}
        yield {"type": "done", "content": "Hello", "model": "m", "prompt_tokens": 5, "completion_tokens": 2}

    # The same decorator and hooks _stream_once uses, applied to a fake provider stream.
    traced = tracing.traced("llm.chat", tracing.LLM, **client._LLM_TRACE_HOOKS)(fake_stream)

    async def consume():
        return [e async for e in traced(_target(), [{"role": "user", "content": "q"}], resolved="m",
                                         temperature=0.7, num_predict=None, keep_alive=None)]

    events = asyncio.run(consume())
    assert [e.get("text") for e in events[:2]] == ["He", "llo"]
    s = [s for s in spans.get_finished_spans() if s.name == "llm.chat"][0]
    assert s.attributes["llm.output_messages.0.message.content"] == "Hello"
    assert s.attributes["llm.token_count.completion"] == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `$PYTEST tests/test_tracing_llm.py -q`
Expected: `AttributeError: 'function' object has no attribute '__traced__'`.

- [ ] **Step 3: Implement**

At the top of `app/llm/client.py`, after the existing imports:

```python
from app.core import tracing


def _llm_start_attributes(target, messages, *, resolved, **_kwargs) -> dict:
    return {
        "llm.model_name": resolved,
        "llm.provider": getattr(target, "provider", None),
        "llm.invocation_parameters": tracing.to_text({k: v for k, v in _kwargs.items() if k in ("temperature", "num_predict", "images")}),
        **tracing.llm_messages_attributes("llm.input_messages", messages),
    }


def _llm_result(result) -> dict:
    # Streams hand the wrapper the list of events; plain calls hand it a dict.
    if isinstance(result, list):
        done = next((e for e in reversed(result) if isinstance(e, dict) and e.get("type") == "done"), {})
        text = done.get("content") or "".join(e.get("text", "") for e in result if isinstance(e, dict) and e.get("type") == "token")
        result = {**done, "content": text}
    tracing.set_attributes(**{
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": tracing.to_text(result.get("content", "")),
        "llm.token_count.prompt": result.get("prompt_tokens"),
        "llm.token_count.completion": result.get("completion_tokens"),
        "llm.finish_reason": result.get("finish_reason"),
        "llm.response_model": result.get("model"),
    })
    return result.get("content", "")


_LLM_TRACE_HOOKS = {"record_args": False, "attributes": _llm_start_attributes, "output": _llm_result}
```

Decorate the three attempt functions (one line each, directly above `async def _chat_once`, `async def _stream_once`, `def _chat_sync_once`):

```python
@tracing.traced("llm.chat", tracing.LLM, **_LLM_TRACE_HOOKS)
```

`images` passed to `_chat_sync_once` are base64 strings: `_llm_start_attributes` only records the key when present through `to_text`, which renders the list; replace that entry with a count:

```python
        "llm.invocation_parameters": tracing.to_text({
            "temperature": _kwargs.get("temperature"),
            "num_predict": _kwargs.get("num_predict"),
            "images": f"<{len(_kwargs['images'])} image(s)>" if _kwargs.get("images") else None,
        }),
```

- [ ] **Step 4: Run to verify pass, plus the existing LLM tests**

Run: `$PYTEST tests/test_tracing_llm.py tests/test_reasoning_truncation_and_rate_limit.py tests/test_llm*.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm/client.py backend/tests/test_tracing_llm.py
git commit -m "feat: trace every LLM provider attempt with messages, answer and tokens"
```

---

### Task 5: Chat pipeline blocks

**Files:**
- Modify (decorator lines only):
  - `backend/app/chat/orchestrator.py`: `handle_ask` (line ~642), `handle_ask_stream` (~905), `_prepare_ask` (~86), `_finalize_ask` (~537), `_run_research_safely` (~482)
  - `backend/app/chat/router.py`: `route_prompt` (~92)
  - `backend/app/chat/local_context.py`: `build_local_context`
  - `backend/app/chat/global_context.py`: `build_global_context`
  - `backend/app/chat/overview_context.py`: `build_overview_context`
  - `backend/app/chat/external_context.py`: `build_external_context`
  - `backend/app/services/retrieval.py`: `search_chunks`, `search_figure_chunks`
  - `backend/app/search/web.py`: `search`, `search_images`
  - `backend/app/chat/research_agent.py`: `run_research_agent`
  - `backend/app/chat/paper_agent.py`: `answer_paper_question`
  - `backend/app/chat/study_agent.py`: `answer_study_question`
  - `backend/app/chat/agent_tools.py`: `read_range`, `run_search`, `run_image`
- Test: `backend/tests/test_tracing_chat.py`

**Interfaces:**
- Consumes: `tracing.traced`, kinds, `tracing.retrieval_attributes`, `tracing.set_attributes`
- Produces: block names below (pinned by the test).

| Function | Block | Kind | Output hook |
| --- | --- | --- | --- |
| `handle_ask` | `ask` | CHAIN | `lambda r: getattr(r, "answer", r)` |
| `handle_ask_stream` | `ask` | CHAIN | final `answer` event text |
| `_prepare_ask` | `prepare` | CHAIN | `lambda p: {"route": p.decision.context_type, "reason": p.decision.reason}` |
| `route_prompt` | `route` | CHAIN | default |
| `build_local_context` / `build_global_context` / `build_overview_context` | `build_context.local` / `.global` / `.overview` | RETRIEVER | `_context_output` |
| `build_external_context` | `build_context.web` | RETRIEVER | `_context_output` |
| `search_chunks` / `search_figure_chunks` | `retrieve` / `retrieve.figures` | RETRIEVER | `_retrieval_output` |
| `web.search` / `web.search_images` | `web_search` / `web_search.images` | TOOL | default |
| `run_research_agent` / `answer_paper_question` / `answer_study_question` | `agent.research` / `agent.paper` / `agent.study` | CHAIN | default |
| `read_range` / `run_search` / `run_image` | `tool:read_range` / `tool:search` / `tool:image` | TOOL | default |
| `_finalize_ask` | `persist` | CHAIN | default |
| `_run_research_safely` | `research` | CHAIN | default |

- [ ] **Step 1: Write the failing test**

```python
import importlib

import pytest

EXPECTED = {
    "app.chat.orchestrator": {"handle_ask": ("ask", "CHAIN"), "handle_ask_stream": ("ask", "CHAIN"),
                              "_prepare_ask": ("prepare", "CHAIN"), "_finalize_ask": ("persist", "CHAIN"),
                              "_run_research_safely": ("research", "CHAIN")},
    "app.chat.router": {"route_prompt": ("route", "CHAIN")},
    "app.chat.local_context": {"build_local_context": ("build_context.local", "RETRIEVER")},
    "app.chat.global_context": {"build_global_context": ("build_context.global", "RETRIEVER")},
    "app.chat.overview_context": {"build_overview_context": ("build_context.overview", "RETRIEVER")},
    "app.chat.external_context": {"build_external_context": ("build_context.web", "RETRIEVER")},
    "app.services.retrieval": {"search_chunks": ("retrieve", "RETRIEVER"),
                               "search_figure_chunks": ("retrieve.figures", "RETRIEVER")},
    "app.search.web": {"search": ("web_search", "TOOL"), "search_images": ("web_search.images", "TOOL")},
    "app.chat.research_agent": {"run_research_agent": ("agent.research", "CHAIN")},
    "app.chat.paper_agent": {"answer_paper_question": ("agent.paper", "CHAIN")},
    "app.chat.study_agent": {"answer_study_question": ("agent.study", "CHAIN")},
    "app.chat.agent_tools": {"read_range": ("tool:read_range", "TOOL"), "run_search": ("tool:search", "TOOL"),
                             "run_image": ("tool:image", "TOOL")},
}


@pytest.mark.parametrize("module,functions", EXPECTED.items())
def test_chat_functions_are_traced_blocks(module, functions):
    loaded = importlib.import_module(module)
    for name, expected in functions.items():
        assert getattr(getattr(loaded, name), "__traced__", None) == expected, f"{module}.{name}"


def test_retrieval_output_lists_chunks_as_documents():
    from app.chat.tracing_hooks import retrieval_output
    from app.core import tracing

    exporter = tracing.use_in_memory_exporter()
    try:
        with tracing.span("retrieve", tracing.RETRIEVER):
            shown = retrieval_output([{"id": "c1", "plain_text": "text", "score": 0.8}])
        s = exporter.get_finished_spans()[0]
        assert s.attributes["retrieval.documents.0.document.id"] == "c1"
        assert shown == {"chunks": 1}
    finally:
        tracing.reset_for_tests()
```

- [ ] **Step 2: Run to verify failure**

Run: `$PYTEST tests/test_tracing_chat.py -q`
Expected: failures listing every function without `__traced__`.

- [ ] **Step 3: Add the shared output hooks**

Create `backend/app/chat/tracing_hooks.py`:

```python
"""Output hooks that turn chat results into readable trace blocks."""

from typing import Any

from app.core import tracing


def retrieval_output(result: Any) -> Any:
    chunks = result if isinstance(result, (list, tuple)) else (result or {}).get("chunks") if isinstance(result, dict) else None
    if isinstance(chunks, (list, tuple)):
        tracing.set_attributes(**tracing.retrieval_attributes(list(chunks)))
        return {"chunks": len(chunks)}
    return result


def context_output(result: Any) -> Any:
    if isinstance(result, dict):
        for key in ("chunks", "sections", "results"):
            if isinstance(result.get(key), (list, tuple)):
                tracing.set_attributes(**tracing.retrieval_attributes(list(result[key])))
        return {k: (f"<{len(v)} items>" if isinstance(v, (list, tuple)) else v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return retrieval_output(result)
    return result


def ask_stream_output(events: list) -> Any:
    for event in reversed(events):
        if isinstance(event, dict) and event.get("answer"):
            return event["answer"]
    return f"<{len(events)} stream events>"
```

- [ ] **Step 4: Add the decorators**

For each row of the table, add one line directly above the function definition, with the import `from app.core import tracing` (and `from app.chat.tracing_hooks import context_output, retrieval_output, ask_stream_output` where used) added to the module's imports. Exact lines:

```python
# orchestrator.py
@tracing.traced("prepare", tracing.CHAIN, output=lambda p: {"route": p.decision.context_type, "reason": p.decision.reason})
async def _prepare_ask(

@tracing.traced("research", tracing.CHAIN)
async def _run_research_safely(prep: _AskPrep) -> Optional[dict]:

@tracing.traced("persist", tracing.CHAIN)
async def _finalize_ask(

@tracing.traced("ask", tracing.CHAIN, output=lambda r: getattr(r, "answer", r))
async def handle_ask(

@tracing.traced("ask", tracing.CHAIN, output=ask_stream_output)
async def handle_ask_stream(

# router.py
@tracing.traced("route", tracing.CHAIN)
async def route_prompt(

# local_context.py / global_context.py / overview_context.py / external_context.py
@tracing.traced("build_context.local", tracing.RETRIEVER, output=context_output)
@tracing.traced("build_context.global", tracing.RETRIEVER, output=context_output)
@tracing.traced("build_context.overview", tracing.RETRIEVER, output=context_output)
@tracing.traced("build_context.web", tracing.RETRIEVER, output=context_output)

# retrieval.py
@tracing.traced("retrieve", tracing.RETRIEVER, output=retrieval_output)
async def search_chunks(
@tracing.traced("retrieve.figures", tracing.RETRIEVER, output=retrieval_output)
async def search_figure_chunks(

# web.py
@tracing.traced("web_search", tracing.TOOL)
async def search(
@tracing.traced("web_search.images", tracing.TOOL)
async def search_images(query: str, *, limit: int = 4) -> list[dict]:

# research_agent.py / paper_agent.py / study_agent.py
@tracing.traced("agent.research", tracing.CHAIN)
@tracing.traced("agent.paper", tracing.CHAIN)
@tracing.traced("agent.study", tracing.CHAIN)

# agent_tools.py
@tracing.traced("tool:read_range", tracing.TOOL)
async def read_range(
@tracing.traced("tool:search", tracing.TOOL)
async def run_search(
@tracing.traced("tool:image", tracing.TOOL)
async def run_image(query: str, *, limit: Optional[int] = None) -> tuple[str, list[dict]]:
```

If `handle_ask_stream` is a coroutine that *returns* an async iterator rather than an async generator function, decorate the inner generator it returns instead, and keep the `ask` name on it; the test pins the attribute on `handle_ask_stream` — in that case set `handle_ask_stream.__traced__ = ("ask", "CHAIN")` only after verifying the inner generator is traced, and note it in the commit message.

- [ ] **Step 5: Run the new test and every chat test**

Run: `$PYTEST tests/test_tracing_chat.py tests/test_strict_document_scope.py tests/test_web_citations.py tests/test_reading_ceiling.py tests/test_thread_compaction_cutoff.py -q`
Expected: all pass (existing tests prove behavior is unchanged with tracing off).

- [ ] **Step 6: Commit**

```bash
git add backend/app/chat backend/app/services/retrieval.py backend/app/search/web.py backend/tests/test_tracing_chat.py
git commit -m "feat: trace chat routing, context, retrieval, web search, agents and tools"
```

---

### Task 6: Ingestion blocks

**Files:**
- Modify (decorator lines only):
  - `backend/app/extraction/pipeline_sync.py`: `resolve_extractor`, `_get_arabic_classification`, `_finish_ingestion`, `run_pipeline_sync`, `run_article_pipeline_sync`
  - `backend/app/extraction/mineru_client.py`: `extract_pdf_sync`, `find_images`
  - `backend/app/extraction/vlm_client.py`: `extract_via_vlm`
  - `backend/app/extraction/chunker.py`: `create_chunks_from_content_list`, `create_chunks_from_markdown`, `crop_code_blocks`
  - `backend/app/extraction/glyph_repair.py`: `repair_chunks`
  - `backend/app/extraction/heading_repair.py`: `repair_headings`
  - `backend/app/extraction/assets.py`: `move_asset_to_storage` (event, not a span)
  - `backend/app/extraction/arabic_ocr.py`: `extract_arabic_document`
  - `backend/app/extraction/arabic_classifier.py`: `classify_document`, `call_local_router`
  - `backend/app/extraction/gemini_ocr_client.py`: `GeminiOcrClient.generate_batch`
  - `backend/app/extraction/arabic_fallback.py`: `GemmaArabicFallback.generate_page`
  - `backend/app/services/article_extraction.py`: `extract_article`, `extract_article_from_html`
  - `backend/app/embeddings/service_sync.py`: `embed_document_chunks_sync`
  - `backend/app/summarization/section_summarizer_sync.py`: `generate_and_store_section_summaries_sync`
  - `backend/app/summarization/figure_describer_sync.py`: `generate_figure_descriptions_sync`
- Create: `backend/app/extraction/tracing_hooks.py`
- Test: `backend/tests/test_tracing_ingestion.py`

**Interfaces:**
- Consumes: `tracing.traced`, `tracing.add_event`, `tracing.set_attributes`
- Produces: block names pinned in the test; `assets` visibility — every `move_asset_to_storage` call adds an `asset_moved` event (source name, stored path, size) to the enclosing block, and `find_images` records how many images the extraction produced.

- [ ] **Step 1: Write the failing tests**

```python
import importlib

import pytest

from app.core import tracing

EXPECTED = {
    "app.extraction.pipeline_sync": {
        "run_pipeline_sync": ("ingest.pdf", "CHAIN"), "run_article_pipeline_sync": ("ingest.article", "CHAIN"),
        "resolve_extractor": ("extract", "CHAIN"), "_get_arabic_classification": ("classify", "CHAIN"),
        "_finish_ingestion": ("persist", "CHAIN"),
    },
    "app.extraction.mineru_client": {"extract_pdf_sync": ("extract.mineru", "CHAIN"), "find_images": ("assets.find", "CHAIN")},
    "app.extraction.vlm_client": {"extract_via_vlm": ("extract.vlm", "CHAIN")},
    "app.extraction.chunker": {
        "create_chunks_from_content_list": ("chunk", "CHAIN"), "create_chunks_from_markdown": ("chunk.markdown", "CHAIN"),
        "crop_code_blocks": ("code_crops", "CHAIN"),
    },
    "app.extraction.glyph_repair": {"repair_chunks": ("glyph_repair", "CHAIN")},
    "app.extraction.heading_repair": {"repair_headings": ("heading_repair", "CHAIN")},
    "app.extraction.arabic_ocr": {"extract_arabic_document": ("extract.arabic", "CHAIN")},
    "app.extraction.arabic_classifier": {"classify_document": ("classify.document", "CHAIN"),
                                         "call_local_router": ("classify.vision", "LLM")},
    "app.services.article_extraction": {"extract_article": ("article.fetch", "TOOL"),
                                        "extract_article_from_html": ("article.extract", "CHAIN")},
    "app.embeddings.service_sync": {"embed_document_chunks_sync": ("embed", "EMBEDDING")},
    "app.summarization.section_summarizer_sync": {"generate_and_store_section_summaries_sync": ("summaries", "CHAIN")},
    "app.summarization.figure_describer_sync": {"generate_figure_descriptions_sync": ("figure_descriptions", "CHAIN")},
}


@pytest.mark.parametrize("module,functions", EXPECTED.items())
def test_ingestion_functions_are_traced_blocks(module, functions):
    loaded = importlib.import_module(module)
    for name, expected in functions.items():
        assert getattr(getattr(loaded, name), "__traced__", None) == expected, f"{module}.{name}"


def test_provider_methods_are_traced():
    from app.extraction.arabic_fallback import GemmaArabicFallback
    from app.extraction.gemini_ocr_client import GeminiOcrClient

    assert GeminiOcrClient.generate_batch.__traced__ == ("llm.gemini_ocr", "LLM")
    assert GemmaArabicFallback.generate_page.__traced__ == ("llm.gemma_ocr", "LLM")


def test_asset_moves_are_events_on_the_enclosing_block(tmp_path, monkeypatch):
    from app.extraction import assets

    exporter = tracing.use_in_memory_exporter()
    try:
        monkeypatch.setattr(assets.settings, "storage_root", str(tmp_path / "storage"))
        image = tmp_path / "fig.jpg"
        image.write_bytes(b"jpeg")
        with tracing.span("ingest.pdf"):
            assets.move_asset_to_storage(image, document_id="5ffce224-80ab-5d5a-b53f-462e57021237")
        s = exporter.get_finished_spans()[0]
        assert [e.name for e in s.events] == ["asset_moved"]
        assert s.events[0].attributes["source"] == "fig.jpg"
    finally:
        tracing.reset_for_tests()


def test_pipeline_run_produces_the_ingestion_tree(db_session_sync, tmp_path, monkeypatch):
    """Reuses the existing success-path fixture shape from test_ingestion_pipeline."""
    from tests.test_ingestion_pipeline import test_run_pipeline_success

    exporter = tracing.use_in_memory_exporter()
    try:
        test_run_pipeline_success(db_session_sync, tmp_path, monkeypatch)
        names = [s.name for s in exporter.get_finished_spans()]
        assert "ingest.pdf" in names and "persist" in names
        root = [s for s in exporter.get_finished_spans() if s.name == "ingest.pdf"][0]
        assert all(s.context.trace_id == root.context.trace_id for s in exporter.get_finished_spans())
    finally:
        tracing.reset_for_tests()
```

- [ ] **Step 2: Run to verify failure**

Run: `$PYTEST tests/test_tracing_ingestion.py -q`
Expected: failures for every untraced function.

- [ ] **Step 3: Add the hooks module**

Create `backend/app/extraction/tracing_hooks.py`:

```python
"""Output hooks that summarize ingestion results for trace blocks."""

from collections import Counter
from typing import Any

from app.core import tracing


def chunks_output(chunks: Any) -> Any:
    if not isinstance(chunks, list):
        return chunks
    by_type = Counter(str(c.get("chunk_type")) for c in chunks if isinstance(c, dict))
    tracing.set_attributes(**{"chunks.count": len(chunks), "chunks.by_type": tracing.to_text(dict(by_type))})
    return {"count": len(chunks), "by_type": dict(by_type), "first": [
        {k: c.get(k) for k in ("sequence_id", "chunk_type", "page_start", "plain_text")} for c in chunks[:5] if isinstance(c, dict)
    ]}


def extractor_output(result: Any) -> Any:
    if isinstance(result, tuple) and len(result) == 2:
        tracing.set_attributes(extractor=str(result[1]))
        return {"output_dir": str(result[0]), "extractor": result[1]}
    return result


def images_output(images: Any) -> Any:
    if isinstance(images, list):
        tracing.set_attributes(**{"assets.found": len(images)})
        return {"found": len(images), "names": [getattr(p, "name", str(p)) for p in images[:20]]}
    return images
```

- [ ] **Step 4: Add the decorators and the asset event**

Decorator lines (each directly above the `def`, with `from app.core import tracing` and the needed hook imports added):

```python
# pipeline_sync.py
@tracing.traced("extract", tracing.CHAIN, output=extractor_output)
def resolve_extractor(
@tracing.traced("classify", tracing.CHAIN)
def _get_arabic_classification(
@tracing.traced("persist", tracing.CHAIN, input=lambda session, **kw: {k: kw.get(k) for k in ("document_id", "job_id", "page_count")} | {"chunks": len(kw.get("chunks") or []), "assets": len(kw.get("asset_map") or {})})
def _finish_ingestion(
@tracing.traced("ingest.pdf", tracing.CHAIN, attributes=lambda session, **kw: {"document.id": str(kw.get("document_id")), "job.id": str(kw.get("job_id"))})
def run_pipeline_sync(
@tracing.traced("ingest.article", tracing.CHAIN)
def run_article_pipeline_sync(

# mineru_client.py
@tracing.traced("extract.mineru", tracing.CHAIN)
def extract_pdf_sync(
@tracing.traced("assets.find", tracing.CHAIN, output=images_output)
def find_images(output_dir: Path) -> list[Path]:

# vlm_client.py
@tracing.traced("extract.vlm", tracing.CHAIN)
def extract_via_vlm(pdf_path: Path, output_dir: Path) -> Path:

# chunker.py
@tracing.traced("chunk", tracing.CHAIN, output=chunks_output)
def create_chunks_from_content_list(content_list_path: Path) -> list[dict]:
@tracing.traced("chunk.markdown", tracing.CHAIN, record_args=False, output=chunks_output)
def create_chunks_from_markdown(markdown_content: str) -> list[dict]:
@tracing.traced("code_crops", tracing.CHAIN, record_args=False)
def crop_code_blocks(chunks: list[dict], pdf_path: Path, images_dir: Path) -> int:

# glyph_repair.py
@tracing.traced("glyph_repair", tracing.CHAIN, record_args=False)
def repair_chunks(chunks: list[dict], pdf_path: Path) -> int:

# heading_repair.py
@tracing.traced("heading_repair", tracing.CHAIN, record_args=False, output=lambda report: report.as_dict())
def repair_headings(chunks: list[dict], outline: Optional[list[dict]] = None) -> RepairReport:

# arabic_ocr.py / arabic_classifier.py
@tracing.traced("extract.arabic", tracing.CHAIN)
def extract_arabic_document(
@tracing.traced("classify.document", tracing.CHAIN)
def classify_document(
@tracing.traced("classify.vision", tracing.LLM, record_args=False)
def call_local_router(

# gemini_ocr_client.py / arabic_fallback.py (methods)
    @tracing.traced("llm.gemini_ocr", tracing.LLM, record_args=False)
    def generate_batch(
    @tracing.traced("llm.gemma_ocr", tracing.LLM, record_args=False)
    def generate_page(

# article_extraction.py
@tracing.traced("article.fetch", tracing.TOOL)
def extract_article(url: str) -> ArticleExtraction:
@tracing.traced("article.extract", tracing.CHAIN, input=lambda html, url: {"url": url, "html_chars": len(html)})
def extract_article_from_html(html: str, url: str) -> ArticleExtraction:

# embeddings/service_sync.py
@tracing.traced("embed", tracing.EMBEDDING)
def embed_document_chunks_sync(

# summarization
@tracing.traced("summaries", tracing.CHAIN)
def generate_and_store_section_summaries_sync(
@tracing.traced("figure_descriptions", tracing.CHAIN)
def generate_figure_descriptions_sync(
```

`record_args=False` on the chunk-list functions keeps a 7,000-chunk list out of the input (the output hook already summarizes it). `extract_article` returns an `ArticleExtraction`; `jsonable` renders dataclass-like objects only through `model_dump`, so add an output hook if `ArticleExtraction` is a dataclass:

```python
def article_output(result: Any) -> Any:
    import dataclasses
    if dataclasses.is_dataclass(result):
        data = dataclasses.asdict(result)
        images = data.get("images") or data.get("image_urls") or []
        tracing.set_attributes(**{"article.images": len(images) if isinstance(images, list) else None})
        return {k: (v if not isinstance(v, str) or len(v) < 2000 else f"<{len(v)} chars>") for k, v in data.items()}
    return result
```

(put it in `app/extraction/tracing_hooks.py` and pass `output=article_output` to both article decorators). The `article.images` count and the `assets.find`/`asset_moved` data are what trace the missing-images report.

In `app/extraction/assets.py`, at the end of `move_asset_to_storage`, just before its `return`:

```python
    tracing.add_event("asset_moved", source=Path(str(img_path)).name, stored=str(meta.get("file_path") if isinstance(meta, dict) else meta))
```

Use the function's actual source-path parameter and return variable names (read the function first); the event must be the last statement before `return` and must not change the returned value.

- [ ] **Step 5: Run the new tests and every ingestion test**

Run: `$PYTEST tests/test_tracing_ingestion.py tests/test_ingestion_pipeline.py tests/test_ingestion_guards.py tests/test_article_ingestion.py tests/test_arabic_pipeline_routing.py tests/test_heading_repair.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/extraction backend/app/services/article_extraction.py backend/app/embeddings/service_sync.py backend/app/summarization backend/tests/test_tracing_ingestion.py
git commit -m "feat: trace ingestion steps from extraction through summaries"
```

---

### Task 7: English isolation proof

**Files:**
- Create: `backend/tests/diff/tracing_harness.py` is **not** committed; the harness lives in the session scratchpad (`diff/harness.py`), as for PR #160.
- Modify: none in `app/`.

- [ ] **Step 1: Full backend and frontend suites**

Run: `$PYTEST tests/ -q` — expected: all pass, count ≥ 949 + the new tracing tests.
Run the frontend check in `node:22-alpine`: `npm ci && npx vitest run && npm run build` — expected: 20 tests pass, build succeeds.

- [ ] **Step 2: Differential harness, tracing off**

Export `origin/main` and this branch (`git archive`), copy into the runner container with the 15 fixtures, and run `harness.py` for main and for the branch with `TRACE_ENABLED=false`. Compare with random image names normalized.
Expected: `diffs vs main: NONE` for paper and `--book` paths.

- [ ] **Step 3: Differential harness, tracing on**

Add to the top of the branch run only:

```python
from app.core import tracing
tracing.use_in_memory_exporter()
```

(via `python -c "from app.core import tracing; tracing.use_in_memory_exporter(); import runpy; runpy.run_path('/tmp/diff/harness.py', run_name='__main__')" …`), and print the number of finished spans at the end.
Expected: `diffs vs main: NONE`, and a non-zero span count with `ingest.pdf`, `chunk`, `glyph_repair`, `heading_repair`, `assets.find`, `persist` present.

- [ ] **Step 4: Record the evidence**

Add a "Verification" section to `docs/runbooks/pipeline-tracing.md` (created in Task 8) with the fixture count, chunk count and both results. Commit with Task 8.

---

### Task 8: Phoenix service, nginx site, deploy step, runbook

**Files:**
- Modify: `backend/docker-compose.prod.yml`, `backend/docker-compose.yml`, `backend/.env.example`, `scripts/deploy-once.sh`
- Create: `backend/nginx/9xaipal-trace.conf`, `docs/runbooks/pipeline-tracing.md`
- Modify: `docs/superpowers/specs/2026-09-26-pipeline-tracing-design.md` §6 (published on `127.0.0.1:6006` instead of "no published port")

- [ ] **Step 1: Phoenix service in `docker-compose.prod.yml`** (after `redis`, before `celery_worker`)

```yaml
  # ------------------------------------------------------------------
  # Phoenix — pipeline trace viewer (docs/runbooks/pipeline-tracing.md).
  # Loopback only; host nginx serves it at https://9xaipal-trace.kl1.site.
  # ------------------------------------------------------------------
  phoenix:
    image: arizephoenix/phoenix:version-20.16.0-nonroot
    container_name: 9xaipal-phoenix
    restart: unless-stopped
    logging: *default-logging
    ports:
      - "127.0.0.1:6006:6006"
    environment:
      PHOENIX_SQL_DATABASE_URL: postgresql://${POSTGRES_USER:-9xaipal}:${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}@postgres:5432/phoenix
      PHOENIX_ENABLE_AUTH: "true"
      PHOENIX_SECRET: ${PHOENIX_SECRET:-}
      PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD: ${PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD:-}
      PHOENIX_DEFAULT_RETENTION_POLICY_DAYS: ${PHOENIX_DEFAULT_RETENTION_POLICY_DAYS:-14}
    depends_on:
      postgres:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"
```

For `api` and `celery_worker` `environment:` blocks, add:

```yaml
      TRACE_ENABLED: ${TRACE_ENABLED:-false}
      TRACE_MAX_TEXT_CHARS: ${TRACE_MAX_TEXT_CHARS:-8000}
      PHOENIX_COLLECTOR_ENDPOINT: http://phoenix:6006/v1/traces
      PHOENIX_API_KEY: ${PHOENIX_API_KEY:-}
```

Do the same in `docker-compose.yml` (dev) with `ports: ["6006:6006"]`, `PHOENIX_ENABLE_AUTH: "false"`, and the dev Postgres credentials.

Validate: `OLLAMA_API_KEY=x POSTGRES_PASSWORD=x docker compose -f docker-compose.prod.yml config -q` and `docker compose -f docker-compose.yml config -q` — expected: no output, exit 0. Confirm with `docker compose … config | grep -c TRACE_ENABLED` → `2` in each file.

- [ ] **Step 2: `.env.example`**

```dotenv
# Pipeline tracing — docs/runbooks/pipeline-tracing.md. Off by default.
TRACE_ENABLED=false
TRACE_MAX_TEXT_CHARS=8000
# Created in Phoenix (Settings → System keys) after first login.
PHOENIX_API_KEY=
# Phoenix service only. SECRET: 32+ characters, at least one digit.
PHOENIX_SECRET=
PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=
PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=14
```

- [ ] **Step 3: nginx site `backend/nginx/9xaipal-trace.conf`**

```nginx
# Pipeline trace viewer (Phoenix). Installed by hand:
#   sudo cp backend/nginx/9xaipal-trace.conf /etc/nginx/sites-available/9xaipal-trace.conf
#   sudo ln -s /etc/nginx/sites-available/9xaipal-trace.conf /etc/nginx/sites-enabled/
#   sudo nginx -t && sudo systemctl reload nginx
#   sudo certbot --nginx -d 9xaipal-trace.kl1.site
server {
    listen 80;
    listen [::]:80;
    server_name 9xaipal-trace.kl1.site;

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:6006;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }
}
```

- [ ] **Step 4: Deploy step `scripts/deploy-once.sh`**

Add a function and call it after every scope except `none`, right after the compose `up` branch:

```bash
ensure_phoenix() {
  # Phoenix keeps its traces in its own database on the shared Postgres.
  (cd "$DEPLOY_DIR/backend" && docker compose -f docker-compose.prod.yml exec -T postgres sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = '"'"'phoenix'"'"'" | grep -q 1 \
     || psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE phoenix"')
  (cd "$DEPLOY_DIR/backend" && docker compose -f docker-compose.prod.yml up -d phoenix)
}
```

Test it locally against the dev compose Postgres by running the function body with `docker-compose.yml`: run twice; expected: first run prints `CREATE DATABASE`, second prints nothing, both exit 0.

- [ ] **Step 5: Runbook `docs/runbooks/pipeline-tracing.md`**

Sections: what it records; enabling (the four rollout steps from spec §8 with exact commands); disabling (`TRACE_ENABLED=false`, `docker compose -f docker-compose.prod.yml up -d api celery_worker`); rotating `PHOENIX_API_KEY`; changing retention; where the data lives (`phoenix` database); resource limits; the Verification evidence from Task 7.

- [ ] **Step 6: Update the spec §6 line and commit**

```bash
git add backend/docker-compose.prod.yml backend/docker-compose.yml backend/.env.example backend/nginx/9xaipal-trace.conf scripts/deploy-once.sh docs/runbooks/pipeline-tracing.md docs/superpowers/specs/2026-09-26-pipeline-tracing-design.md
git commit -m "feat: run Phoenix as the trace viewer behind 9xaipal-trace.kl1.site"
```

---

### Task 9: Pull request, deploy, and live verification

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin feat/pipeline-tracing
gh pr create --draft --base main --title "Pipeline tracing: OpenTelemetry + Phoenix (off by default)" --body-file <body>
```

Body: what it adds, the isolation evidence from Task 7, the rollout steps, and the kill switch. Wait for CI (`gh pr checks`) to pass. The owner merges; the self-hosted runner deploys.

- [ ] **Step 2: Server secrets (after merge, over SSH, values never printed)**

```bash
ssh -o BatchMode=yes kl1@176.31.27.95 'cd ~/apps/9xaipal/backend && cp -p .env .env.bak-before-tracing-$(date +%Y%m%d%H%M%S) && umask 077 && {
  printf "\n# Pipeline tracing (Phoenix)\nTRACE_ENABLED=false\nPHOENIX_SECRET=%s1\nPHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=%s\nPHOENIX_DEFAULT_RETENTION_POLICY_DAYS=14\n" \
    "$(openssl rand -hex 24)" "$(openssl rand -base64 18 | tr -d /+=)"
} >> .env && chmod 600 .env && grep -c "^PHOENIX_" .env'
```

Then restart Phoenix so it reads them: `docker compose -f docker-compose.prod.yml up -d phoenix`. Give the owner the initial admin password through the terminal only (`grep PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD .env` on the server, run by the owner).

- [ ] **Step 3: DNS and certificate (owner, needs sudo)**

Check `dig +short A 9xaipal-trace.kl1.site @1.1.1.1` returns `176.31.27.95`. Then the owner runs the four commands at the top of `backend/nginx/9xaipal-trace.conf`. Verify: `curl -sI https://9xaipal-trace.kl1.site | head -1` → `HTTP/2 200` (or a redirect to the login page).

- [ ] **Step 4: Connect the app**

In Phoenix (browser): log in, change the admin password, create a system API key. Put it in the server `.env` as `PHOENIX_API_KEY=…` (over SSH, stdin, not printed), set `TRACE_ENABLED=true`, then `docker compose -f docker-compose.prod.yml up -d api celery_worker`.

- [ ] **Step 5: Live check**

Upload one small English PDF and ask one question about it in `https://9xaipal.kl1.site`. In Phoenix, confirm: a `POST /api/v1/papers/upload` trace containing `task:process_ingestion` → `ingest.pdf` → `extract` … `persist`, then `task:embed_document` / summaries in the same trace; and a `POST /api/v1/ask…` trace containing `ask` → `prepare` → `route` → `build_context.*` → `llm.chat` with messages and tokens. Record screenshots in the PR.

---

## Self-Review Notes

- Spec coverage: §4.1 → Task 1; §4.2 chat rows → Task 5, LLM → Task 4, ingestion/tasks → Tasks 3 and 6; §5 shapes → Tasks 3–6 tests; §5.2 caps/redaction → Task 1; §5.3 Celery linking → Task 3; §6 Phoenix → Task 8; §7 security → Tasks 2, 8, 9; §8 rollout → Task 9; §9.1 → Task 1; §9.2 → Tasks 1–7.
- Block names are defined once per task table and pinned by that task's structural test; later tasks do not rename them.
- The one spec change (loopback-published Phoenix port) is applied in Task 8, Step 6.
