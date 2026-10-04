"""Real-socket coverage for the app's SSE endpoints.

ASGITransport collects the whole response body before returning it, so these
tests run the application under Uvicorn and consume it over TCP. The LLM is
replaced with a delayed local fake; no provider can be contacted.
"""

import asyncio
import json
import socket
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import uvicorn
from sqlalchemy import text

from app.api import deps
from app.chat import orchestrator
from app.main import app
from app.schemas.chat import AskResponse
from app.llm import client as llm_client


TOKENS = tuple(f"word{i:02d} " for i in range(19)) + ("word19",)
ANSWER = "".join(TOKENS)
TOKEN_DELAY = 0.1


@pytest.fixture
def auth_override():
    def install(user_id):
        async def current_user():
            return {"id": user_id}

        app.dependency_overrides[deps.get_current_user] = current_user

    yield install
    app.dependency_overrides.clear()


@pytest.fixture
async def socket_client():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)

    server = uvicorn.Server(
        uvicorn.Config(app, lifespan="off", log_level="critical", access_log=False)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("Uvicorn stopped before it started")
            await asyncio.sleep(0.01)

        port = sock.getsockname()[1]
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=None, trust_env=False
        ) as client:
            yield client
    finally:
        server.should_exit = True
        await task
        sock.close()


async def _seed_document(db_session, doc_kind: str):
    user_id = (
        await db_session.execute(
            text("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
            {"e": f"{uuid4()}@test.local"},
        )
    ).scalar_one()
    document_id = (
        await db_session.execute(
            text("""
                INSERT INTO documents
                    (user_id, filename, original_filename, title, status, doc_kind)
                VALUES (:user, :filename, :filename, 'Socket test', 'complete', :kind)
                RETURNING id
            """),
            {"user": user_id, "filename": f"{uuid4()}.pdf", "kind": doc_kind},
        )
    ).scalar_one()
    for sequence_id, body in (
        (1, "Chapter 1: opening material visible to the reader."),
        (2, "FUTURE SPOILER: this block is beyond the reading ceiling."),
    ):
        await db_session.execute(
            text("""
                INSERT INTO chunks
                    (document_id, sequence_id, chunk_type, markdown, plain_text, page_start, token_count)
                VALUES (:document, :sequence, 'text', :body, :body, 1, 8)
            """),
            {"document": document_id, "sequence": sequence_id, "body": body},
        )
    await db_session.commit()
    return user_id, document_id


def _install_delayed_llm(monkeypatch):
    prompts = []

    def remember_prompt(messages):
        prompts.append(json.dumps(messages, ensure_ascii=False, default=str))

    async def fake_chat(messages, *, model=None, temperature=0.3, **kwargs):
        remember_prompt(messages)
        for _ in TOKENS:
            await asyncio.sleep(TOKEN_DELAY)
        return {"content": ANSWER, "model": "socket-fake"}

    async def fake_stream_chat(messages, *, model=None, temperature=0.3, **kwargs):
        remember_prompt(messages)
        for token in TOKENS:
            await asyncio.sleep(TOKEN_DELAY)
            yield {"type": "token", "text": token}
        yield {"type": "done", "content": ANSWER, "model": "socket-fake"}

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    monkeypatch.setattr(llm_client, "stream_chat", fake_stream_chat)
    return prompts


async def _read_events(client, method: str, path: str, *, json_body=None):
    started = time.monotonic()
    events = []
    async with client.stream(method, path, json=json_body) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                events.append((time.monotonic() - started, json.loads(line[5:].strip())))
    return events


def _assert_incremental(events, surface: str):
    token_events = [(at, event) for at, event in events if event.get("type") == "token"]
    done_time, done_event = next((at, event) for at, event in events if event.get("type") == "done")
    times = [at for at, _ in token_events]
    print(f"{surface}: token arrivals={times}; done={done_time:.3f}s", flush=True)
    assert len(token_events) >= 3, (
        f"{surface}: expected multiple token events; arrivals={times}, done={done_time:.3f}s"
    )
    assert times[0] < done_time - 0.3, (
        f"{surface}: first token was not well before done; arrivals={times}, done={done_time:.3f}s"
    )
    assert times[-1] - times[0] > 0.5, (
        f"{surface}: token arrivals were not spread over time; arrivals={times}"
    )
    assert done_event.get("answer") == ANSWER


async def _no_memories(_session=None, **kwargs):
    return []


def _disable_external_work(monkeypatch):
    from app.chat import paper_agent, study_agent
    from app.api.v1.endpoints import ask as ask_endpoint
    from app.api.v1.endpoints import notes as notes_endpoint
    from app.api.v1.endpoints import studies as studies_endpoint

    monkeypatch.setattr(paper_agent, "recall_memories", _no_memories)
    monkeypatch.setattr(study_agent, "recall_memories", _no_memories)
    monkeypatch.setattr(paper_agent.web_search, "is_configured", lambda: False)
    monkeypatch.setattr(study_agent.web_search, "is_configured", lambda: False)
    monkeypatch.setattr(ask_endpoint.grounding, "enabled", lambda: False)
    monkeypatch.setattr(notes_endpoint.grounding, "enabled", lambda: False)
    monkeypatch.setattr(studies_endpoint.grounding, "enabled", lambda: False)


def _stub_paper_ask_finalization(monkeypatch):
    from app.chat.router import RouterDecision

    async def prepare(_session, **kwargs):
        prep = orchestrator._AskPrep(
            prompt=kwargs["prompt"],
            user_id=kwargs["user_id"],
            document_id=kwargs["document_id"],
            conversation_id=kwargs["conversation_id"] or uuid4(),
            parent_turn_id=kwargs["parent_turn_id"],
            thread_root_turn_id=kwargs["thread_root_turn_id"],
            is_sub_thread=bool(kwargs["parent_turn_id"] or kwargs["thread_root_turn_id"]),
            start_time=time.time(),
            decision=RouterDecision(context_type="LOCAL", reason="socket test"),
            messages=[{"role": "user", "content": kwargs["prompt"]}],
        )
        return prep

    async def finalize(_session, prep, *, answer, model, **kwargs):
        return AskResponse(
            answer=answer,
            context_type="LOCAL",
            router_reason="socket test",
            citations=[],
            model=model,
            conversation_id=prep.conversation_id,
        )

    monkeypatch.setattr(orchestrator, "_prepare_ask", prepare)
    monkeypatch.setattr(orchestrator, "_finalize_ask", finalize)


@pytest.mark.asyncio
async def test_paper_ask_tokens_arrive_incrementally(
    db_session, auth_override, socket_client, monkeypatch
):
    user_id, document_id = await _seed_document(db_session, "paper")
    auth_override(user_id)
    _disable_external_work(monkeypatch)
    _stub_paper_ask_finalization(monkeypatch)
    _install_delayed_llm(monkeypatch)

    events = await _read_events(
        socket_client,
        "POST",
        f"/api/v1/papers/{document_id}/ask/stream",
        json_body={"query": "Explain the result"},
    )
    _assert_incremental(events, "paper ask")


@pytest.mark.asyncio
async def test_book_ask_tokens_arrive_incrementally_with_reading_ceiling(
    db_session, auth_override, socket_client, monkeypatch
):
    user_id, document_id = await _seed_document(db_session, "book")
    auth_override(user_id)
    _disable_external_work(monkeypatch)
    _stub_paper_ask_finalization(monkeypatch)
    prompts = _install_delayed_llm(monkeypatch)

    events = await _read_events(
        socket_client,
        "POST",
        f"/api/v1/papers/{document_id}/ask/stream",
        json_body={"query": "What has happened so far?", "max_sequence_id": 1},
    )
    _assert_incremental(events, "book ask")
    assert prompts and all("FUTURE SPOILER" not in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_book_tool_probe_clears_draft_before_streaming_final_answer(
    db_session, auth_override, socket_client, monkeypatch
):
    user_id, document_id = await _seed_document(db_session, "book")
    auth_override(user_id)
    _disable_external_work(monkeypatch)
    _stub_paper_ask_finalization(monkeypatch)
    monkeypatch.setattr(
        orchestrator, "settings", SimpleNamespace(paper_agent_holistic_max_steps=1)
    )

    calls = 0
    prompts = []

    async def unexpected_nonstream_call(*args, **kwargs):
        raise AssertionError("book agent probe should use the streaming client")

    async def stream_with_tool_round(messages, *, model=None, temperature=0.3, **kwargs):
        nonlocal calls
        calls += 1
        prompts.append(json.dumps(messages, ensure_ascii=False, default=str))
        if calls == 1:
            pieces = ("A provisional thought before the lookup. ", "<tool>", "READ: 1-1", "</tool>")
            content = "".join(pieces)
        else:
            pieces = TOKENS
            content = ANSWER
        for piece in pieces:
            await asyncio.sleep(0.03)
            yield {"type": "token", "text": piece}
        yield {"type": "done", "content": content, "model": "socket-fake"}

    monkeypatch.setattr(llm_client, "chat", unexpected_nonstream_call)
    monkeypatch.setattr(llm_client, "stream_chat", stream_with_tool_round)

    events = await _read_events(
        socket_client,
        "POST",
        f"/api/v1/papers/{document_id}/ask/stream",
        json_body={"query": "Read this chapter", "max_sequence_id": 1},
    )
    event_types = [event["type"] for _, event in events]
    assert "replace" in event_types
    replace_index = event_types.index("replace")
    assert "step" in event_types[replace_index + 1:]
    assert any(event["type"] == "token" for _, event in events[:replace_index])
    streamed_after_replace = "".join(
        event["text"] for _, event in events[replace_index + 1:] if event.get("type") == "token"
    )
    assert streamed_after_replace == ANSWER
    assert all("FUTURE SPOILER" not in prompt for prompt in prompts)
    final = next(event for _, event in events if event.get("type") == "done")
    assert final["answer"] == ANSWER
    assert calls == 2


@pytest.mark.asyncio
async def test_notes_stream_tokens_arrive_incrementally(
    db_session, auth_override, socket_client, monkeypatch
):
    user_id, document_id = await _seed_document(db_session, "article")
    auth_override(user_id)
    _disable_external_work(monkeypatch)
    _install_delayed_llm(monkeypatch)

    events = await _read_events(
        socket_client,
        "POST",
        f"/api/v1/papers/{document_id}/notes/stream",
        json_body={
            "question": "Explain this passage",
            "anchor": {"kind": "text", "sequence_id": 1, "quote": "opening material"},
            "margin_side": "left",
        },
    )
    _assert_incremental(events, "notes stream")


@pytest.mark.asyncio
async def test_study_chat_tokens_arrive_incrementally(
    db_session, auth_override, socket_client, monkeypatch
):
    user_id, _document_id = await _seed_document(db_session, "paper")
    auth_override(user_id)
    _disable_external_work(monkeypatch)
    _install_delayed_llm(monkeypatch)

    events = await _read_events(
        socket_client,
        "POST",
        "/api/v1/studies/library/chat/stream",
        json_body={"question": "Summarize the collection", "new_conversation": True},
    )
    _assert_incremental(events, "study chat")


@pytest.mark.asyncio
async def test_reference_stream_sends_queue_progress_before_completion(
    db_session, auth_override, socket_client, monkeypatch
):
    from app.api.v1.endpoints import chunks as chunks_endpoint

    user_id, document_id = await _seed_document(db_session, "paper")
    await db_session.execute(
        text("INSERT INTO paper_references (document_id, ref_number, raw_text) VALUES (:d, 1, 'Example reference')"),
        {"d": document_id},
    )
    await db_session.commit()
    auth_override(user_id)
    _disable_external_work(monkeypatch)

    async def fake_resolve(_session, _paper_id, _ref_number, _row, *, on_queued):
        await on_queued(SimpleNamespace(position=2, wait_seconds=0.2))
        await asyncio.sleep(0.25)

        class Entry:
            def model_dump(self, mode="json"):
                return {"resolve_status": "resolved"}

        return Entry()

    monkeypatch.setattr(chunks_endpoint, "_resolve_row", fake_resolve)
    events = await _read_events(
        socket_client,
        "GET",
        f"/api/v1/papers/{document_id}/references/1/resolve/stream",
    )
    queued = next((at, event) for at, event in events if event.get("type") == "queued")
    resolved = next((at, event) for at, event in events if event.get("type") == "resolved")
    print(
        f"references: queued={queued[0]:.3f}s resolved={resolved[0]:.3f}s",
        flush=True,
    )
    assert queued[0] < resolved[0] - 0.1
