"""Multi-key rotation for Tavily (search) and Ollama (chat).

Several providers give a free allowance PER KEY, so the way to get more
headroom is more keys rather than a paid plan. Both TAVILY_API_KEY and
OLLAMA_API_KEY therefore accept a comma-separated list and rotate: an
exhausted or rejected key falls through to the next one carrying the SAME
request, and only once every key is spent does the caller see a failure.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.core import circuit_breaker
from app.core.config import settings
from app.llm import resolver
from app.search import tavily_client
from app.search.errors import ProviderError


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "llm_provider", "auto")
    yield
    circuit_breaker.reset()


# ── the parser ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("", []),
    ("solo", ["solo"]),
    ("a,b,c", ["a", "b", "c"]),
    ("  a , b ,c ", ["a", "b", "c"]),           # stray whitespace
    ("a,,b,", ["a", "b"]),                       # blanks/trailing comma dropped
    ("\na\n,\nb\n", ["a", "b"]),                # a wrapped .env line
])
def test_key_list_parsing(raw, expected, monkeypatch):
    """A trailing comma or wrapped line must not inject an empty key that
    would fail every request it served."""
    monkeypatch.setattr(settings, "tavily_api_key", raw)
    assert settings.tavily_api_keys == expected
    monkeypatch.setattr(settings, "ollama_api_key", raw)
    assert settings.ollama_api_keys == expected


# ── Tavily rotation ─────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {"results": [{"title": "t", "url": "u", "content": "c"}]}
        self.text = "body"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)

    def json(self):
        return self._data


def _post_by_key(behavior: dict):
    """Fake transport dispatching on which key's Bearer token was sent."""
    seen = []

    async def fake_post(self, url, json=None, headers=None, **kw):
        key = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        seen.append(key)
        return _Resp(behavior.get(key, 200))

    return fake_post, seen


async def test_exhausted_key_rotates_to_the_next(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "k1,k2,k3")
    fake, seen = _post_by_key({"k1": 429})   # k1 out of quota
    monkeypatch.setattr(httpx.AsyncClient, "post", fake)

    results = await tavily_client.search("query")

    assert seen == ["k1", "k2"], "should stop at the first key that works"
    assert results and results[0]["source_engine"] == "tavily"


async def test_rotates_past_every_rejected_key(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "k1,k2,k3")
    fake, seen = _post_by_key({"k1": 429, "k2": 401})
    monkeypatch.setattr(httpx.AsyncClient, "post", fake)

    assert await tavily_client.search("query")
    assert seen == ["k1", "k2", "k3"]


async def test_all_keys_exhausted_raises_so_the_cascade_moves_on(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "k1,k2")
    fake, seen = _post_by_key({"k1": 429, "k2": 429})
    monkeypatch.setattr(httpx.AsyncClient, "post", fake)

    with pytest.raises(ProviderError):
        await tavily_client.search("query")
    assert seen == ["k1", "k2"]


async def test_server_error_does_not_burn_the_other_keys(monkeypatch):
    """A 500 is Tavily being down, not a key problem — every other key would
    hit the same wall, so don't spend a request per key proving it."""
    monkeypatch.setattr(settings, "tavily_api_key", "k1,k2,k3")
    fake, seen = _post_by_key({"k1": 500})
    monkeypatch.setattr(httpx.AsyncClient, "post", fake)

    with pytest.raises(ProviderError):
        await tavily_client.search("query")
    assert seen == ["k1"], "a server error must not rotate"


async def test_a_spent_key_is_skipped_on_later_calls(monkeypatch):
    """Once a key has tripped its breaker, later searches must not keep
    paying a round-trip to rediscover it's dead."""
    monkeypatch.setattr(settings, "tavily_api_key", "k1,k2")
    fake, seen = _post_by_key({"k1": 429})
    monkeypatch.setattr(httpx.AsyncClient, "post", fake)

    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        await tavily_client.search("query")
    seen.clear()

    await tavily_client.search("query")
    assert seen == ["k2"], "the spent key should no longer be tried"


async def test_single_key_behaves_exactly_as_before(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "only")
    fake, seen = _post_by_key({})
    monkeypatch.setattr(httpx.AsyncClient, "post", fake)

    assert await tavily_client.search("query")
    assert seen == ["only"]


async def test_no_key_configured_returns_empty_without_calling(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "")
    post = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    assert await tavily_client.search("query") == []
    post.assert_not_called()


# ── Ollama rotation ─────────────────────────────────────────────────────

def test_each_ollama_key_becomes_its_own_cascade_entry(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "o1,o2,o3")
    targets = resolver.llm_cascade_sync(ollama_up=True)

    assert [t.provider for t in targets] == ["ollama", "ollama", "ollama"]
    assert [t.api_key for t in targets] == ["o1", "o2", "o3"]
    # Distinct breaker ids, or one spent key would take the others down.
    assert [t.breaker_id for t in targets] == ["ollama#0", "ollama#1", "ollama#2"]


def test_ollama_keys_come_before_cloud_providers(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "o1,o2")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    targets = resolver.llm_cascade_sync(ollama_up=True)

    assert [t.breaker_id for t in targets] == ["ollama#0", "ollama#1", "openai#0"]


def test_no_ollama_key_still_yields_one_keyless_target(monkeypatch):
    """A local Ollama needs no key — the no-key case must behave exactly as
    it did before multi-key support existed."""
    monkeypatch.setattr(settings, "ollama_api_key", "")
    targets = resolver.llm_cascade_sync(ollama_up=True)

    assert len(targets) == 1
    assert targets[0].provider == "ollama"
    assert targets[0].api_key == ""


def test_a_tripped_ollama_key_is_skipped_but_the_others_remain(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "o1,o2,o3")
    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        circuit_breaker.record_failure("ollama#0")

    targets = resolver.llm_cascade_sync(ollama_up=True)

    assert [t.breaker_id for t in targets] == ["ollama#1", "ollama#2"]


async def test_each_ollama_target_sends_its_OWN_key_not_the_joined_string(monkeypatch):
    """Regression, caught only by a live test: ollama_client used to read
    settings.ollama_api_key directly instead of taking the key from its
    LLMTarget. With a comma-separated list that sent the WHOLE joined
    string as one Bearer token, so every key 401'd — including the valid
    one — and the cascade "correctly" fell through all of them to failure.
    """
    from app.llm import client as llm_client, ollama_client

    monkeypatch.setattr(settings, "ollama_api_key", "dead1,dead2,good")
    monkeypatch.setattr(settings, "ollama_base_url", "https://ollama.example")

    seen_keys = []

    async def fake_chat(messages, *, model=None, temperature=0.7, stream=False,
                        num_predict=None, keep_alive=None, api_key=None):
        seen_keys.append(api_key)
        if api_key != "good":
            from app.api.errors import ModelUnavailable
            raise ModelUnavailable(f"{model} (401: Unauthorized)")
        return {"content": "ok", "model": model, "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(ollama_client, "chat", fake_chat)
    monkeypatch.setattr(resolver, "ollama_reachable", AsyncMock(return_value=True))

    result = await llm_client.chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "ok"
    # Each attempt must carry ONE key, in order — never the joined string.
    assert seen_keys == ["dead1", "dead2", "good"]
    assert "dead1,dead2,good" not in seen_keys


def test_headers_never_send_the_joined_key_string(monkeypatch):
    """The header builder itself must never emit the raw comma-joined
    setting, on any path — including the keyless probe/catalog calls."""
    from app.llm import ollama_client

    monkeypatch.setattr(settings, "ollama_api_key", "k1,k2,k3")

    explicit = ollama_client._ollama_headers("k2")
    assert explicit["Authorization"] == "Bearer k2"

    # No key given (probe/catalog): falls back to the FIRST key, not the join.
    fallback = ollama_client._ollama_headers()
    assert fallback["Authorization"] == "Bearer k1"
    assert "," not in fallback["Authorization"]
