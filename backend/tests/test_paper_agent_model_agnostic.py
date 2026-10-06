from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.chat import guardrail, paper_agent
from app.core.config import settings


MODEL = "meta/muse-glimmer-30b"
DOCUMENT_ID = uuid4()
USER_ID = uuid4()
CHUNKS = [{
    "sequence_id": 1,
    "plain_text": "The method improves retrieval accuracy.",
    "markdown": "The method improves retrieval accuracy.",
    "token_count": 8,
    "page_start": 1,
    "chunk_type": "text",
}]


def _install_paper_stubs(monkeypatch, replies):
    monkeypatch.setattr(
        paper_agent.chunk_repo, "get_all_document_chunks",
        AsyncMock(return_value=CHUNKS),
    )
    monkeypatch.setattr(paper_agent, "recall_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(paper_agent.web_search, "is_configured", lambda: False)
    monkeypatch.setattr(settings, "paper_whole_document_context", False)

    async def fake_stream_answer(_messages, **_kwargs):
        reply = replies.pop(0)
        for event in reply:
            yield event

    monkeypatch.setattr(paper_agent, "stream_answer", fake_stream_answer)


def _invoke_agent():
    return paper_agent.answer_paper_question(
        None,
        document_id=DOCUMENT_ID,
        user_id=USER_ID,
        question="What does the method improve?",
        anchor={"kind": "block", "sequence_id": 1, "quote": CHUNKS[0]["plain_text"]},
        model=MODEL,
        max_steps=1,
        allow_web=False,
    )


@pytest.mark.asyncio
async def test_direct_muse_answer_forwards_notice_and_actual_model(monkeypatch):
    notice = {"type": "notice", "message": "GLM 5.3 Flash couldn't answer, so Muse Glimmer 30B answered instead."}
    answer = "The method improves retrieval accuracy."
    _install_paper_stubs(monkeypatch, [[
        notice,
        {"type": "token", "text": answer},
        {"type": "_final", "answer": answer, "raw": answer, "model": MODEL,
         "notes": [], "remembers": []},
    ]])

    events = [event async for event in _invoke_agent()]

    assert notice in events
    assert events.index(notice) < next(i for i, event in enumerate(events) if event["type"] == "token")
    done = next(event for event in events if event["type"] == "done")
    assert done["answer"] == answer
    assert done["model"] == MODEL


@pytest.mark.asyncio
async def test_muse_text_tool_protocol_and_final_notice_work_in_agent_path(monkeypatch):
    notice = {"type": "notice", "message": "GLM 5.3 Flash couldn't answer, so Muse Glimmer 30B answered instead."}
    answer = "The method improves retrieval accuracy. [[1]]"
    _install_paper_stubs(monkeypatch, [
        [{"type": "_final", "answer": "", "raw": "<tool>READ: 1-1</tool>",
          "model": MODEL, "notes": [], "remembers": []}],
        [notice,
         {"type": "token", "text": answer},
         {"type": "_final", "answer": answer, "raw": answer, "model": MODEL,
          "notes": [], "remembers": []}],
    ])
    async def run_call(_session, _document_id, _chunks, call, **_kwargs):
        return {**call, "observation": "The method improves retrieval accuracy."}

    run_call = AsyncMock(side_effect=run_call)
    monkeypatch.setattr(paper_agent, "_run_call", run_call)

    events = [event async for event in _invoke_agent()]

    run_call.assert_awaited_once()
    assert run_call.await_args.args[3]["tool"] == "READ"
    assert notice in events
    assert events.index(notice) < next(i for i, event in enumerate(events) if event["type"] == "token")
    done = next(event for event in events if event["type"] == "done")
    assert done["answer"] == answer
    assert done["model"] == MODEL


@pytest.mark.asyncio
async def test_unsupported_muse_tool_response_reaches_forced_answer(monkeypatch):
    answer = "Here is the answer without an unsupported tool."
    _install_paper_stubs(monkeypatch, [
        [{"type": "_final", "answer": "", "raw": "<tool>UNSUPPORTED: do work</tool>",
          "model": MODEL, "notes": [], "remembers": []}],
        [{"type": "token", "text": answer},
         {"type": "_final", "answer": answer, "raw": answer, "model": MODEL,
          "notes": [], "remembers": []}],
    ])
    run_call = AsyncMock()
    monkeypatch.setattr(paper_agent, "_run_call", run_call)

    events = [event async for event in _invoke_agent()]

    run_call.assert_not_awaited()
    done = next(event for event in events if event["type"] == "done")
    assert done["answer"] == answer
    assert done["model"] == MODEL


@pytest.mark.asyncio
async def test_guardrail_treats_length_cutoff_as_no_verdict_not_denial(monkeypatch):
    monkeypatch.setattr(
        guardrail.llm_client, "chat",
        AsyncMock(return_value={"content": "", "finish_reason": "length"}),
    )

    assert await guardrail.is_topic_allowed("Explain the paper's retrieval method.") is True
