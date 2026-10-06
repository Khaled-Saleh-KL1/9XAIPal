from unittest.mock import AsyncMock

import pytest

from app.api.errors import ModelUnavailable
from app.chat import agent_tools
from app.chat.study_agent import parse_tool_calls
from app.core import circuit_breaker
from app.llm import resolver
from app.llm.resolver import LLMTarget


@pytest.fixture
def clean_breaker():
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


def _target(provider):
    return LLMTarget(
        provider=provider,
        api_key="test-key",
        base_url=f"https://{provider}.example.com/v1",
        chat_model="test-model",
        classifier_model="test-model",
        vlm_model="test-model",
    )


def _install_fake_cascade(monkeypatch, attempts):
    targets = [_target(provider) for provider in attempts]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))
    calls = []

    def fake_stream_once(target, _messages, **_kwargs):
        calls.append(target.provider)
        chunks, failure = attempts[target.provider]

        async def events():
            for chunk in chunks:
                yield {"type": "token", "text": chunk}
            if failure:
                raise ModelUnavailable(f"{target.provider} (connection reset)")
            content = "".join(chunks)
            yield {"type": "done", "content": content, "model": target.provider}

        return events()

    monkeypatch.setattr(agent_tools.llm_client, "_stream_once", fake_stream_once)
    return calls


async def _stream_answer(monkeypatch, chunks, *, final_content=None, **options):
    async def fake_stream_chat(*_args, **_kwargs):
        for chunk in chunks:
            yield {"type": "token", "text": chunk}
        yield {
            "type": "done",
            "content": final_content if final_content is not None else "".join(chunks),
            "model": "test-model",
        }

    monkeypatch.setattr(agent_tools.llm_client, "stream_chat", fake_stream_chat)
    return [event async for event in agent_tools.stream_answer([], **options)]


@pytest.mark.asyncio
async def test_final_delta_remember_tag_keeps_following_prose_streamed(monkeypatch):
    chunks = ["Opening text ", "<remember>reader preference</remember> Closing sentence."]

    events = await _stream_answer(monkeypatch, chunks, catch_remember=True)

    streamed = "".join(event["text"] for event in events if event["type"] == "token")
    final = next(event for event in events if event["type"] == "_final")
    assert streamed == "Opening text  Closing sentence."
    assert final["answer"] == "Opening text  Closing sentence."
    assert final["remembers"] == ["reader preference"]


@pytest.mark.asyncio
async def test_adjacent_final_delta_note_and_remember_tags_keep_suffix(monkeypatch):
    chunks = ["Opening text ", "<note all>save this</note><remember>reader preference</remember> Closing sentence."]

    events = await _stream_answer(
        monkeypatch, chunks, catch_notes=True, catch_remember=True
    )

    streamed = "".join(event["text"] for event in events if event["type"] == "token")
    final = next(event for event in events if event["type"] == "_final")
    assert streamed == "Opening text  Closing sentence."
    assert final["answer"] == "Opening text  Closing sentence."
    assert final["notes"] == [{"body": "save this", "board": "universal"}]
    assert final["remembers"] == ["reader preference"]


@pytest.mark.asyncio
async def test_split_remember_tag_still_streams_prose_after_closing_tag(monkeypatch):
    chunks = ["Opening text <rem", "ember>reader preference</rem", "ember> Closing sentence."]

    events = await _stream_answer(monkeypatch, chunks, catch_remember=True)

    streamed = "".join(event["text"] for event in events if event["type"] == "token")
    final = next(event for event in events if event["type"] == "_final")
    assert streamed == "Opening text  Closing sentence."
    assert final["answer"] == "Opening text  Closing sentence."
    assert final["remembers"] == ["reader preference"]


@pytest.mark.asyncio
async def test_terminal_content_completes_a_skipped_tag_and_flushes_suffix(monkeypatch):
    chunks = ["Opening text ", "<remember>reader preference</rem"]
    final_content = "Opening text <remember>reader preference</remember> Closing sentence."

    events = await _stream_answer(
        monkeypatch, chunks, final_content=final_content, catch_remember=True
    )

    streamed = "".join(event["text"] for event in events if event["type"] == "token")
    final = next(event for event in events if event["type"] == "_final")
    assert streamed == "Opening text  Closing sentence."
    assert final["answer"] == "Opening text  Closing sentence."
    assert final["remembers"] == ["reader preference"]


@pytest.mark.asyncio
async def test_tool_probe_does_not_log_forced_final_warning(monkeypatch, caplog):
    chunks = ["Visible text <tool>SEARCH: query</tool>"]
    caplog.set_level("WARNING", logger=agent_tools.logger.name)

    await _stream_answer(monkeypatch, chunks, tool_probe=True)

    assert not any("forced final turn" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_stream_retries_after_only_a_withheld_prefix(monkeypatch, clean_breaker):
    calls = _install_fake_cascade(
        monkeypatch,
        {
            "openai": (["draft"], True),
            "anthropic": (["Answer from provider B."], False),
        },
    )

    events = [event async for event in agent_tools.stream_answer([])]

    streamed = "".join(event["text"] for event in events if event["type"] == "token")
    final = next(event for event in events if event["type"] == "_final")
    assert streamed == "Answer from provider B."
    assert final["answer"] == "Answer from provider B."
    assert calls == ["openai", "anthropic"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_provider_text",
    ["<tool>SEARCH: stale", "<tool>SEARCH: stale</tool>"],
)
async def test_stream_retries_after_hidden_tool_text_and_parses_fallback_call(
    monkeypatch, clean_breaker, failed_provider_text
):
    calls = _install_fake_cascade(
        monkeypatch,
        {
            "openai": ([failed_provider_text], True),
            "anthropic": (["<tool>SEARCH: fallback query</tool>"], False),
        },
    )

    events = [event async for event in agent_tools.stream_answer([], tool_probe=True)]

    streamed = "".join(event["text"] for event in events if event["type"] == "token")
    final = next(event for event in events if event["type"] == "_final")
    calls_parsed = parse_tool_calls(final["raw"])
    assert streamed == ""
    assert calls_parsed["searches"] == ["fallback query"]
    assert calls == ["openai", "anthropic"]


@pytest.mark.asyncio
async def test_stream_still_raises_after_visible_text_without_trying_next_provider(
    monkeypatch, clean_breaker
):
    calls = _install_fake_cascade(
        monkeypatch,
        {
            "openai": (["Provider A has shown visible text."], True),
            "anthropic": (["must not appear"], False),
        },
    )
    visible = []

    with pytest.raises(ModelUnavailable) as exc_info:
        async for event in agent_tools.stream_answer([]):
            if event["type"] == "token":
                visible.append(event["text"])

    assert "openai" in exc_info.value.model
    assert "".join(visible).startswith("Provider A has shown")
    assert calls == ["openai"]


@pytest.mark.asyncio
async def test_model_fallback_notice_is_forwarded_before_answer_tokens(monkeypatch):
    async def fake_stream_chat(*_args, **_kwargs):
        yield {
            "type": "notice",
            "message": "GLM 5.3 Flash couldn't answer, so Gemma 4 31B answered instead.",
        }
        yield {"type": "token", "text": "A grounded answer."}
        yield {"type": "done", "content": "A grounded answer.", "model": "gemma4:31b"}

    monkeypatch.setattr(agent_tools.llm_client, "stream_chat", fake_stream_chat)

    events = [event async for event in agent_tools.stream_answer([])]

    assert events[0]["type"] == "notice"
    assert events[1]["type"] == "token"
    assert events[-1]["type"] == "_final"
    assert events[-1]["model"] == "gemma4:31b"
