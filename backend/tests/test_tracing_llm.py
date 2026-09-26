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
    assert "llm.token_count.completion" in s.attributes
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
