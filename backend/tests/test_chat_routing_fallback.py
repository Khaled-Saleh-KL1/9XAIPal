from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

import app.chat.orchestrator as orchestrator
from app.chat.prompts import DIAGRAM_INSTRUCTIONS, LOCAL_SYSTEM_PROMPT
from app.chat.router import RouterDecision


def _chunk(markdown: str, sequence_id: int = 7) -> dict:
    return {
        "id": uuid4(),
        "sequence_id": sequence_id,
        "page_start": 3,
        "page_end": 3,
        "similarity": 0.91,
        "markdown": markdown,
        "plain_text": markdown,
    }


@pytest.fixture
def ask_dependencies(monkeypatch):
    monkeypatch.setattr(orchestrator, "is_topic_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        orchestrator,
        "route_prompt",
        AsyncMock(return_value=RouterDecision("LOCAL", "specific equation", 0.9)),
    )
    monkeypatch.setattr(orchestrator, "get_conversation_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(orchestrator, "_user_explicitly_mentioned_other_field", lambda _prompt: False)
    monkeypatch.setattr(orchestrator, "_user_wants_figure", lambda _prompt: False)
    monkeypatch.setattr(orchestrator, "wants_outside_context", lambda _prompt: False)


@pytest.mark.asyncio
async def test_local_without_position_uses_global_retrieval_in_answer_messages(
    monkeypatch, ask_dependencies
):
    global_builder = AsyncMock(
        return_value={"chunks": [_chunk("GLOBAL RETRIEVED PAPER EVIDENCE")], "assets": []}
    )
    monkeypatch.setattr(orchestrator, "build_global_context", global_builder)
    local_builder = AsyncMock()
    monkeypatch.setattr(orchestrator, "build_local_context", local_builder)

    prep = await orchestrator._prepare_ask(
        None,
        user_id=uuid4(),
        prompt="لماذا يقسم البحث حاصل الضرب النقطي على الجذر التربيعي لـ d_k؟",
        document_id=uuid4(),
        document={"strict_scope": True, "filename": "paper.pdf"},
    )

    global_builder.assert_awaited_once()
    local_builder.assert_not_awaited()
    assert prep.decision.context_type == "GLOBAL"
    assert prep.route_fallback == "local_without_position"
    assert "GLOBAL RETRIEVED PAPER EVIDENCE" in prep.messages[-1]["content"]
    assert "هذه المعلومات غير موجودة في الأقسام المسترجعة من البحث." in prep.messages[0]["content"]


@pytest.mark.asyncio
async def test_local_with_position_and_text_keeps_local_context(
    monkeypatch, ask_dependencies
):
    chunk_id = uuid4()
    local_builder = AsyncMock(
        return_value={"chunks": [_chunk("LOCAL SECTION EVIDENCE")], "assets": []}
    )
    global_builder = AsyncMock()
    monkeypatch.setattr(orchestrator, "build_local_context", local_builder)
    monkeypatch.setattr(orchestrator, "build_global_context", global_builder)

    prep = await orchestrator._prepare_ask(
        None,
        user_id=uuid4(),
        prompt="Explain this equation",
        document_id=uuid4(),
        current_chunk_id=chunk_id,
        document={"strict_scope": True, "filename": "paper.pdf"},
    )

    local_builder.assert_awaited_once()
    global_builder.assert_not_awaited()
    assert prep.decision.context_type == "LOCAL"
    assert prep.route_fallback is None
    assert "LOCAL SECTION EVIDENCE" in prep.messages[-1]["content"]
    assert prep.messages[0]["content"] == LOCAL_SYSTEM_PROMPT + "\n\n" + DIAGRAM_INSTRUCTIONS


_plain_text_only_chunk = _chunk("")
_plain_text_only_chunk["plain_text"] = "Not included by the local markdown formatter"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "local_chunks",
    [[], [_chunk("")], [_plain_text_only_chunk]],
)
async def test_local_context_without_usable_text_uses_global(
    monkeypatch, ask_dependencies, local_chunks
):
    global_builder = AsyncMock(
        return_value={"chunks": [_chunk("GLOBAL RETRIEVED PAPER EVIDENCE")], "assets": []}
    )
    monkeypatch.setattr(orchestrator, "build_local_context", AsyncMock(
        return_value={"chunks": local_chunks, "assets": []}
    ))
    monkeypatch.setattr(orchestrator, "build_global_context", global_builder)

    prep = await orchestrator._prepare_ask(
        None,
        user_id=uuid4(),
        prompt="Explain this equation",
        document_id=uuid4(),
        current_chunk_id=uuid4(),
        document={"strict_scope": True, "filename": "paper.pdf"},
    )

    global_builder.assert_awaited_once()
    assert prep.decision.context_type == "GLOBAL"
    assert prep.route_fallback == "local_context_empty"
    assert "GLOBAL RETRIEVED PAPER EVIDENCE" in prep.messages[-1]["content"]


@pytest.mark.asyncio
async def test_arabic_question_gets_arabic_refusal_in_local_prompt(
    monkeypatch, ask_dependencies
):
    monkeypatch.setattr(orchestrator, "build_local_context", AsyncMock(
        return_value={"chunks": [_chunk("LOCAL SECTION EVIDENCE")], "assets": []}
    ))
    monkeypatch.setattr(orchestrator, "build_global_context", AsyncMock())

    prep = await orchestrator._prepare_ask(
        None,
        user_id=uuid4(),
        prompt="اشرح هذه المعادلة بالتفصيل",
        document_id=uuid4(),
        current_chunk_id=uuid4(),
        document={"strict_scope": True, "filename": "paper.pdf"},
    )

    system_prompt = prep.messages[0]["content"]
    assert "لا أملك معلومات كافية في القسم الحالي للإجابة عن هذا السؤال. هل تريدني أن أبحث في بقية البحث؟" in system_prompt
    assert "I don't have enough information in the current section" not in system_prompt


@pytest.mark.asyncio
async def test_english_refusal_sentence_remains_unchanged(
    monkeypatch, ask_dependencies
):
    monkeypatch.setattr(orchestrator, "build_local_context", AsyncMock(
        return_value={"chunks": [_chunk("LOCAL SECTION EVIDENCE")], "assets": []}
    ))
    monkeypatch.setattr(orchestrator, "build_global_context", AsyncMock())

    prep = await orchestrator._prepare_ask(
        None,
        user_id=uuid4(),
        prompt="Explain this equation in detail",
        document_id=uuid4(),
        current_chunk_id=uuid4(),
        document={"strict_scope": True, "filename": "paper.pdf"},
    )

    assert prep.messages[0]["content"] == LOCAL_SYSTEM_PROMPT + "\n\n" + DIAGRAM_INSTRUCTIONS
