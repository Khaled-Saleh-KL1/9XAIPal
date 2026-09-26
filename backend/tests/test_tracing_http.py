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
