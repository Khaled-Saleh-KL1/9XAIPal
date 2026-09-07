"""NVIDIA NIM integration: multi-key rotation, the meta/muse-glimmer-30b
model pin, and the per-key 40 RPM rate limit.

NVIDIA is the cascade's last resort (CLOUD_PROVIDER_ORDER's final entry) and
also directly selectable: requesting its pinned model by name (see
resolver.MODEL_PROVIDER_PINS) skips the Ollama probe and every other cloud
provider entirely, since none of them could serve that model anyway.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.errors import ModelUnavailable, NoLLMConfigured
from app.core import circuit_breaker
from app.core.config import settings
from app.llm import resolver


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "llm_provider", "auto")
    resolver.reset_resolution_cache()
    yield
    circuit_breaker.reset()
    resolver.reset_resolution_cache()


# ── key parsing + target construction ──────────────────────────────────

def test_nvidia_key_list_parses_like_ollama(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", " k1 ,k2,,k3,")
    assert settings.nvidia_api_keys == ["k1", "k2", "k3"]


def test_each_nvidia_key_becomes_its_own_target(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "n1,n2,n3")
    targets = resolver._nvidia_targets()

    # Order rotates per call so the keys are three budgets rather than one
    # plus two spares (see _nvidia_targets), so assert the SET — what matters
    # is that every key is present, each with its own breaker.
    assert [t.provider for t in targets] == ["nvidia", "nvidia", "nvidia"]
    assert sorted(t.api_key for t in targets) == ["n1", "n2", "n3"]
    assert sorted(t.breaker_id for t in targets) == ["nvidia#0", "nvidia#1", "nvidia#2"]
    assert all(t.chat_model == settings.nvidia_chat_model for t in targets)
    # A key keeps its own index whichever position it rotates into.
    assert {t.api_key: t.breaker_id for t in targets} == {
        "n1": "nvidia#0", "n2": "nvidia#1", "n3": "nvidia#2",
    }


def test_no_nvidia_key_yields_no_targets(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "")
    assert resolver._nvidia_targets() == []


# ── cascade placement ───────────────────────────────────────────────────

def test_nvidia_is_the_cascade_backstop_after_ollama_and_other_cloud(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "nvidia_api_key", "n1,n2")

    targets = resolver.llm_cascade_sync(ollama_up=True)

    # NVIDIA sits behind both, and which of its keys leads rotates per call.
    assert [t.provider for t in targets] == ["ollama", "openai", "nvidia", "nvidia"]
    assert sorted(t.breaker_id for t in targets[2:]) == ["nvidia#0", "nvidia#1"]
    assert [t.breaker_id for t in targets[:2]] == ["ollama#0", "openai#0"]


def test_nvidia_only_appears_when_a_key_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "nvidia_api_key", "")

    targets = resolver.llm_cascade_sync(ollama_up=True)

    assert "nvidia" not in [t.provider for t in targets]


# ── the model pin fast path ─────────────────────────────────────────────

def test_pinned_model_skips_straight_to_nvidia(monkeypatch):
    """Ollama reachable, OpenAI configured, but the request names the pinned
    model — it must go directly to NVIDIA, not through either of them."""
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "nvidia_api_key", "n1,n2")

    targets = resolver.targets_for_sync("meta/muse-glimmer-30b", ollama_up=True)

    assert [t.provider for t in targets] == ["nvidia", "nvidia"]
    assert sorted(t.api_key for t in targets) == ["n1", "n2"]


async def test_pinned_model_skips_straight_to_nvidia_async(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "n1")
    monkeypatch.setattr(resolver, "ollama_reachable", AsyncMock(return_value=True))

    targets = await resolver.targets_for("meta/muse-glimmer-30b")

    assert [t.provider for t in targets] == ["nvidia"]


def test_unpinned_model_falls_through_to_the_normal_cascade(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "n1")

    targets = resolver.targets_for_sync("some-other-model", ollama_up=True)

    assert [t.provider for t in targets] == ["ollama", "nvidia"]


def test_no_model_argument_uses_the_normal_cascade(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "n1")

    targets = resolver.targets_for_sync(None, ollama_up=True)

    assert [t.provider for t in targets] == ["ollama", "nvidia"]


def test_pinned_model_with_no_key_configured_raises(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "")

    with pytest.raises(NoLLMConfigured):
        resolver.targets_for_sync("meta/muse-glimmer-30b", ollama_up=True)


def test_pinned_model_respects_the_circuit_breaker(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "n1,n2")
    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        circuit_breaker.record_failure("nvidia#0")

    targets = resolver.targets_for_sync("meta/muse-glimmer-30b", ollama_up=True)

    assert [t.breaker_id for t in targets] == ["nvidia#1"]


async def test_client_chat_uses_the_pin_when_model_is_explicit(monkeypatch):
    """End-to-end through app.llm.client.chat(): explicitly requesting the
    pinned model must dispatch to NVIDIA even though Ollama is reachable."""
    from app.llm import client as llm_client, ollama_client

    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "n1")
    monkeypatch.setattr(resolver, "ollama_reachable", AsyncMock(return_value=True))
    monkeypatch.setattr(resolver, "throttle_nvidia_key", AsyncMock())

    ollama_chat = AsyncMock(side_effect=AssertionError("must not call Ollama"))
    monkeypatch.setattr(ollama_client, "chat", ollama_chat)

    async def fake_post(self, url, json=None, headers=None, **kw):
        assert "integrate.api.nvidia.com" in url
        assert headers["Authorization"] == "Bearer n1"
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": "hi"}}],
            "model": "meta/muse-glimmer-30b",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await llm_client.chat(
        [{"role": "user", "content": "hi"}], model="meta/muse-glimmer-30b",
    )

    assert result["content"] == "hi"
    ollama_chat.assert_not_called()


async def test_client_chat_throttles_nvidia_before_the_request(monkeypatch):
    from app.llm import client as llm_client

    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "n1")
    monkeypatch.setattr(resolver, "ollama_reachable", AsyncMock(return_value=True))

    calls: list[str] = []
    throttle = AsyncMock(side_effect=lambda key_index: calls.append("throttle"))
    monkeypatch.setattr(resolver, "throttle_nvidia_key", throttle)

    async def fake_post(self, url, json=None, headers=None, **kw):
        calls.append("post")
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": "hi"}}],
            "model": "meta/muse-glimmer-30b",
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    await llm_client.chat([{"role": "user", "content": "hi"}], model="meta/muse-glimmer-30b")

    assert calls == ["throttle", "post"]


# ── the per-key rate limiter itself ─────────────────────────────────────
#
# Real (tiny) sleeps rather than mocking asyncio.sleep/time.sleep directly —
# those are process-global functions, and patching them risks affecting
# unrelated concurrent machinery (the test event loop itself). A few
# hundredths of a second per test is a fine trade for that safety.

async def test_throttle_nvidia_key_enforces_the_minimum_interval(monkeypatch):
    monkeypatch.setattr(resolver, "_NVIDIA_MIN_INTERVAL_SECONDS", 0.1)

    start = resolver.time.monotonic()
    await resolver.throttle_nvidia_key(7)
    await resolver.throttle_nvidia_key(7)
    elapsed = resolver.time.monotonic() - start

    assert elapsed >= 0.1


async def test_throttle_nvidia_key_does_not_serialize_different_keys(monkeypatch):
    monkeypatch.setattr(resolver, "_NVIDIA_MIN_INTERVAL_SECONDS", 5.0)

    start = resolver.time.monotonic()
    await resolver.throttle_nvidia_key(8)
    await resolver.throttle_nvidia_key(9)  # a different key: independent budget
    elapsed = resolver.time.monotonic() - start

    assert elapsed < 1.0, "a different key must not wait on key 8's budget"


def test_throttle_nvidia_key_sync_enforces_the_minimum_interval(monkeypatch):
    monkeypatch.setattr(resolver, "_NVIDIA_MIN_INTERVAL_SECONDS", 0.1)

    start = resolver.time.monotonic()
    resolver.throttle_nvidia_key_sync(7)
    resolver.throttle_nvidia_key_sync(7)
    elapsed = resolver.time.monotonic() - start

    assert elapsed >= 0.1


# ── catalog picker ───────────────────────────────────────────────────────

async def test_catalog_surfaces_the_pinned_model_when_nvidia_is_configured(monkeypatch):
    from app.llm import catalog

    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "n1")
    monkeypatch.setattr(resolver, "ollama_reachable", AsyncMock(return_value=False))

    result = await catalog.list_chat_models()

    assert "meta/muse-glimmer-30b" in [m["name"] for m in result["models"]]


async def test_catalog_omits_the_pinned_model_without_a_key(monkeypatch):
    from app.llm import catalog

    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(resolver, "ollama_reachable", AsyncMock(return_value=False))

    result = await catalog.list_chat_models()

    assert "meta/muse-glimmer-30b" not in [m["name"] for m in result["models"]]
