"""Unit tests for app.llm.client's provider cascade: chat()/stream_chat()/
chat_sync() must retry the SAME messages against the next configured LLM
backend when one fails, mirroring the web search cascade
(tests/test_web_search_cascade.py) — this is what "hold the prompt and
resend it to the second API" means in code.

streaming has one deliberate asymmetry from the other two: no fallback once
a token has already reached the caller (see client.py's own docstring for
why) — several tests here exist specifically to pin that behavior down.
"""

from unittest.mock import AsyncMock

import pytest

from app.api.errors import ModelUnavailable
from app.core import circuit_breaker
from app.llm import client, ollama_client, resolver
from app.llm.resolver import LLMTarget


@pytest.fixture(autouse=True)
def clean_breaker():
    """The circuit breaker keeps module-level state — reset it around every
    test or a tripped provider leaks into the next one and the failure looks
    like a cascade bug."""
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


def _target(provider: str) -> LLMTarget:
    return LLMTarget(
        provider=provider,
        api_key="test-key",
        base_url=f"https://{provider}.example.com/v1",
        chat_model="test-model",
        classifier_model="test-model",
        vlm_model="test-model",
    )


def _ok_response(content: str = "hello") -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "model": "test-model",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


class _FakeHTTPResponse:
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json = json_data
        self.text = str(json_data)

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


# ─────────────────────────────────────────────────────────────────────────────
# chat() — no partial-output concern, every failure is a clean fall-through
# ─────────────────────────────────────────────────────────────────────────────

async def test_chat_falls_through_when_first_target_raises(monkeypatch):
    targets = [_target("openai"), _target("anthropic")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))

    calls = []

    async def fake_chat_once(target, messages, **kwargs):
        calls.append(target.provider)
        if target.provider == "openai":
            raise ModelUnavailable("openai (500: server error)")
        return {"content": "answer from anthropic", "model": "test-model",
                "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)

    result = await client.chat([{"role": "user", "content": "hi"}])

    assert calls == ["openai", "anthropic"]
    assert result["content"] == "answer from anthropic"


async def test_chat_stops_at_first_success_without_trying_the_rest(monkeypatch):
    targets = [_target("openai"), _target("anthropic"), _target("xai")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))

    calls = []

    async def fake_chat_once(target, messages, **kwargs):
        calls.append(target.provider)
        return {"content": f"answer from {target.provider}", "model": "m",
                "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)

    result = await client.chat([{"role": "user", "content": "hi"}])

    assert calls == ["openai"]
    assert result["content"] == "answer from openai"


async def test_chat_raises_after_every_target_fails(monkeypatch):
    targets = [_target("openai"), _target("anthropic")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))

    async def always_fails(target, messages, **kwargs):
        raise ModelUnavailable(f"{target.provider} (429: rate limited)")

    monkeypatch.setattr(client, "_chat_once", always_fails)

    with pytest.raises(ModelUnavailable) as exc_info:
        await client.chat([{"role": "user", "content": "hi"}])
    # The LAST provider's error is what surfaces, not the first — the most
    # recent failure is the most relevant one to show/log.
    assert "anthropic" in str(exc_info.value.model)


async def test_chat_same_messages_object_reaches_every_target(monkeypatch):
    """The literal point of the cascade: the SAME prompt gets resent, not a
    truncated or mutated copy."""
    targets = [_target("openai"), _target("anthropic")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))

    seen_messages = []

    async def fake_chat_once(target, messages, **kwargs):
        seen_messages.append(messages)
        if target.provider == "openai":
            raise ModelUnavailable("openai (500)")
        return {"content": "ok", "model": "m", "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)

    original = [{"role": "user", "content": "the exact same prompt"}]
    await client.chat(original)

    assert len(seen_messages) == 2
    assert seen_messages[0] == original
    assert seen_messages[1] == original


async def test_chat_ollama_failure_falls_through_to_cloud(monkeypatch):
    """The concrete gap this cascade closes: Ollama passing its reachability
    probe doesn't mean the real completion call succeeds (wrong model
    pulled, OOM, mid-generation crash) — that failure must not be fatal."""
    targets = [_target("ollama"), _target("openai")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))

    async def fake_chat_once(target, messages, **kwargs):
        if target.provider == "ollama":
            raise ModelUnavailable("gemma4:31b (Ollama unreachable: model not found)")
        return {"content": "cloud saved the day", "model": "m", "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)

    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "cloud saved the day"


# ─────────────────────────────────────────────────────────────────────────────
# stream_chat() — fallback only before the first token reaches the caller
# ─────────────────────────────────────────────────────────────────────────────

async def _events(*events):
    for e in events:
        yield e


async def test_stream_falls_through_when_target_fails_before_any_token(monkeypatch):
    targets = [_target("openai"), _target("anthropic")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))

    def fake_stream_once(target, messages, **kwargs):
        if target.provider == "openai":
            async def gen():
                raise ModelUnavailable("openai (503: overloaded)")
                yield  # pragma: no cover — makes this a generator
            return gen()
        return _events(
            {"type": "token", "text": "hi"},
            {"type": "done", "content": "hi", "model": "m", "prompt_tokens": None, "completion_tokens": None},
        )

    monkeypatch.setattr(client, "_stream_once", fake_stream_once)

    collected = [e async for e in client.stream_chat([{"role": "user", "content": "hi"}])]

    assert collected[0] == {"type": "token", "text": "hi"}
    assert collected[-1]["type"] == "done"


async def test_stream_does_not_retry_after_a_token_was_already_yielded(monkeypatch):
    """The core streaming guarantee: once the caller has seen real output,
    a later failure on that SAME provider must surface as an error, not
    silently restart on a different provider mid-answer."""
    targets = [_target("openai"), _target("anthropic")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))

    anthropic_called = False

    def fake_stream_once(target, messages, **kwargs):
        if target.provider == "openai":
            async def gen():
                yield {"type": "token", "text": "partial answer"}
                raise ModelUnavailable("openai (connection reset mid-stream)")
            return gen()
        nonlocal anthropic_called
        anthropic_called = True
        return _events({"type": "token", "text": "should never appear"})

    monkeypatch.setattr(client, "_stream_once", fake_stream_once)

    with pytest.raises(ModelUnavailable) as exc_info:
        _ = [e async for e in client.stream_chat([{"role": "user", "content": "hi"}])]

    assert "openai" in str(exc_info.value.model)
    assert anthropic_called is False


async def test_stream_raises_when_every_target_fails_before_any_token(monkeypatch):
    targets = [_target("openai"), _target("anthropic")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))

    def always_fails(target, messages, **kwargs):
        async def gen():
            raise ModelUnavailable(f"{target.provider} (500)")
            yield  # pragma: no cover
        return gen()

    monkeypatch.setattr(client, "_stream_once", always_fails)

    with pytest.raises(ModelUnavailable):
        _ = [e async for e in client.stream_chat([{"role": "user", "content": "hi"}])]


# ─────────────────────────────────────────────────────────────────────────────
# chat_sync() — Celery worker path, same shape as chat()
# ─────────────────────────────────────────────────────────────────────────────

def test_chat_sync_falls_through_on_failure(monkeypatch):
    targets = [_target("openai"), _target("anthropic")]
    monkeypatch.setattr(resolver, "llm_cascade_sync", lambda: targets)

    def fake_chat_sync_once(target, messages, **kwargs):
        if target.provider == "openai":
            raise ModelUnavailable("openai (429)")
        return {"content": "sync fallback worked", "model": "m", "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(client, "_chat_sync_once", fake_chat_sync_once)

    result = client.chat_sync([{"role": "user", "content": "hi"}])
    assert result["content"] == "sync fallback worked"


def test_chat_sync_raises_after_every_target_fails(monkeypatch):
    targets = [_target("openai")]
    monkeypatch.setattr(resolver, "llm_cascade_sync", lambda: targets)
    monkeypatch.setattr(client, "_chat_sync_once", lambda t, m, **k: (_ for _ in ()).throw(ModelUnavailable("openai (500)")))

    with pytest.raises(ModelUnavailable):
        client.chat_sync([{"role": "user", "content": "hi"}])


# ─────────────────────────────────────────────────────────────────────────────
# is_available() — true if ANY cascade target answers
# ─────────────────────────────────────────────────────────────────────────────

async def test_is_available_true_when_second_target_answers(monkeypatch):
    targets = [_target("ollama"), _target("openai")]
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(return_value=targets))
    monkeypatch.setattr(ollama_client, "is_available", AsyncMock(return_value=False))

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _FakeHTTPResponse(200, {})

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: FakeAsyncClient())

    assert await client.is_available() is True


async def test_is_available_false_when_nothing_configured(monkeypatch):
    from app.api.errors import NoLLMConfigured
    monkeypatch.setattr(resolver, "llm_cascade", AsyncMock(side_effect=NoLLMConfigured("no backend")))
    assert await client.is_available() is False
