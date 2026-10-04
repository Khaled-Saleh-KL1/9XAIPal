import pytest

from app.chat import agent_tools


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
