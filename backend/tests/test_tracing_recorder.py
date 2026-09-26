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


def test_traced_sync_generator_forwards_events_unchanged(spans):
    events = [{"type": "token", "text": "Hel"}, {"type": "token", "text": "lo"}, {"type": "done", "content": "Hello"}]

    @tracing.traced("sync_stream", output=lambda collected: "".join(e.get("text", "") for e in collected))
    def stream():
        for event in events:
            yield event

    received = list(stream())
    assert received == events
    assert all(a is b for a, b in zip(received, events))
    assert _by_name(spans, "sync_stream").attributes["output.value"] == "Hello"


def test_traced_sync_generator_close_marks_cancelled(spans):
    @tracing.traced("sync_cut")
    def stream():
        yield 1
        yield 2

    gen = stream()
    assert next(gen) == 1
    gen.close()

    s = _by_name(spans, "sync_cut")
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


def test_record_input_output_are_free_when_tracing_is_off(monkeypatch):
    tracing.reset_for_tests()
    monkeypatch.setattr(tracing.settings, "trace_enabled", False)

    calls = []

    def watch(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("to_text must not be called when there is no recording span")

    monkeypatch.setattr(tracing, "to_text", watch)

    tracing.record_input({"a": 1})
    tracing.record_output({"b": 2})

    assert calls == []


def test_record_input_output_are_free_outside_a_span(spans, monkeypatch):
    calls = []

    def watch(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("to_text must not be called with no current span")

    monkeypatch.setattr(tracing, "to_text", watch)

    tracing.record_input({"a": 1})
    tracing.record_output({"b": 2})

    assert calls == []


def test_record_input_output_set_value_and_mime_type_inside_a_span(spans):
    with tracing.span("recorded") as current:
        tracing.record_input({"a": 1})
        tracing.record_output("done")

    s = _by_name(spans, "recorded")
    assert '"a": 1' in s.attributes["input.value"]
    assert s.attributes["input.mime_type"] == "application/json"
    assert s.attributes["output.value"] == "done"
    assert s.attributes["output.mime_type"] == "text/plain"
