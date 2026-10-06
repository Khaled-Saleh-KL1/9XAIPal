from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.chat import study_agent


@pytest.mark.asyncio
async def test_study_answer_forwards_fallback_notice_and_actual_model(monkeypatch):
    document_id = uuid4()
    user_id = uuid4()
    model = "gpt-oss:120b"
    answer = "The paper reports a retrieval improvement."
    notice = {
        "type": "notice",
        "message": "Muse Glimmer 30B couldn't answer, so GPT OSS 120B answered instead.",
    }

    monkeypatch.setattr(
        study_agent.chunk_repo, "get_all_chunks_for_documents",
        AsyncMock(return_value={document_id: []}),
    )
    monkeypatch.setattr(
        study_agent.summary_repo, "get_gists_for_documents",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(study_agent, "recall_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(study_agent.web_search, "is_configured", lambda: False)

    async def no_note_events(*_args, **_kwargs):
        if False:
            yield {}

    monkeypatch.setattr(study_agent, "_pin_written_notes", no_note_events)

    async def fake_stream_answer(_messages, **_kwargs):
        yield notice
        yield {"type": "token", "text": answer}
        yield {
            "type": "_final", "answer": answer, "raw": answer,
            "model": model, "notes": [], "remembers": [],
        }

    monkeypatch.setattr(study_agent, "stream_answer", fake_stream_answer)

    events = [
        event async for event in study_agent.answer_study_question(
            None,
            user_id=user_id,
            papers=[{"id": document_id, "title": "Synthetic paper", "page_count": 1}],
            question="What improved?",
            model=model,
            max_steps=1,
            allow_web=False,
        )
    ]

    assert notice in events
    assert events.index(notice) < next(i for i, event in enumerate(events) if event["type"] == "token")
    done = next(event for event in events if event["type"] == "done")
    assert done["answer"] == answer
    assert done["model"] == model
