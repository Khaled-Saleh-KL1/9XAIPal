"""Unit tests for AI backend auto-detection (app.llm.resolver).

The Ollama probe result is injected via ``ollama_up`` so no test depends on
whether Ollama is actually running on the machine.
"""

import pytest

from app.api.errors import ModelUnavailable, NoLLMConfigured
from app.core.config import settings
from app.llm import resolver


@pytest.fixture(autouse=True)
def clean_resolver_state(monkeypatch):
    """Blank keys, auto mode, fresh caches — each test opts into its setup."""
    resolver.reset_resolution_cache()
    monkeypatch.setattr(settings, "llm_provider", "auto")
    monkeypatch.setattr(settings, "embedding_provider", "auto")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "embedding_base_url", "")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    for provider in resolver.CLOUD_PROVIDER_ORDER:
        monkeypatch.setattr(settings, f"{provider}_api_key", "")
    yield
    resolver.reset_resolution_cache()


def test_auto_uses_ollama_when_reachable():
    target = resolver.resolve_llm_sync(ollama_up=True)
    assert target.provider == "ollama"
    assert target.base_url == settings.ollama_base_url
    assert target.chat_model == settings.chat_model
    assert target.classifier_model == settings.effective_classifier_model
    assert target.vlm_model == settings.effective_vlm_model


def test_ollama_targets_carry_api_key_when_set(monkeypatch):
    """resolver must forward OLLAMA_API_KEY onto both target types, or the
    Bearer auth wired into ollama_client.py / embeddings/model.py silently
    becomes dead code (headers built from target.api_key would always be
    empty)."""
    monkeypatch.setattr(settings, "ollama_api_key", "sk-test")
    llm_target = resolver.resolve_llm_sync(ollama_up=True)
    assert llm_target.provider == "ollama"
    assert llm_target.api_key == "sk-test"

    embedding_target = resolver.resolve_embedding_sync(ollama_up=True)
    assert embedding_target.provider == "ollama"
    assert embedding_target.api_key == "sk-test"


def test_ollama_targets_api_key_blank_when_unset():
    # clean_resolver_state already blanks settings.ollama_api_key; this is
    # the companion case to test_ollama_targets_carry_api_key_when_set.
    llm_target = resolver.resolve_llm_sync(ollama_up=True)
    assert llm_target.api_key == ""

    embedding_target = resolver.resolve_embedding_sync(ollama_up=True)
    assert embedding_target.api_key == ""


def test_auto_falls_back_to_first_key_when_ollama_down(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    target = resolver.resolve_llm_sync(ollama_up=False)
    assert target.provider == "anthropic"
    assert target.api_key == "sk-ant-test"
    assert target.base_url == resolver.PROVIDER_BASE_URLS["anthropic"]
    # Cloud fallback must use the provider's own model, never the Ollama tag.
    assert target.chat_model == settings.anthropic_chat_model
    assert target.classifier_model == target.chat_model
    assert target.vlm_model == target.chat_model


def test_auto_walks_providers_in_documented_order(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-ds-test")
    monkeypatch.setattr(settings, "openai_api_key", "sk-oa-test")
    target = resolver.resolve_llm_sync(ollama_up=False)
    assert target.provider == "openai"  # openai precedes deepseek in the chain


def test_auto_with_nothing_configured_gives_instructions():
    with pytest.raises(NoLLMConfigured) as exc_info:
        resolver.resolve_llm_sync(ollama_up=False)
    message = str(exc_info.value.model)
    assert "OPENAI_API_KEY" in message
    assert "Ollama" in message
    assert settings.ollama_base_url in message


def test_pinned_cloud_provider_skips_probe(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_key", "sk-generic")
    # ollama_up=True must not matter when the provider is pinned.
    target = resolver.resolve_llm_sync(ollama_up=True)
    assert target.provider == "openai"
    assert target.api_key == "sk-generic"
    assert target.chat_model == settings.openai_chat_model


def test_pinned_cloud_provider_without_key_fails_clearly(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "xai")
    with pytest.raises(ModelUnavailable) as exc_info:
        resolver.resolve_llm_sync()
    assert "XAI_API_KEY" in str(exc_info.value.model)


# ─────────────────────────────────────────────────────────────────────────────
# llm_cascade_sync / llm_cascade: every target "auto" would try, in order —
# consumed by app.llm.client's retry loop (chat/stream_chat/chat_sync).
# ─────────────────────────────────────────────────────────────────────────────

def test_cascade_includes_ollama_first_when_reachable(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    targets = resolver.llm_cascade_sync(ollama_up=True)
    assert [t.provider for t in targets] == ["ollama", "anthropic"]


def test_cascade_skips_ollama_when_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-oa-test")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    targets = resolver.llm_cascade_sync(ollama_up=False)
    assert [t.provider for t in targets] == ["openai", "anthropic"]


def test_cascade_includes_every_configured_cloud_provider_in_order(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-ds-test")
    monkeypatch.setattr(settings, "openai_api_key", "sk-oa-test")
    monkeypatch.setattr(settings, "xai_api_key", "xai-test")
    targets = resolver.llm_cascade_sync(ollama_up=False)
    # CLOUD_PROVIDER_ORDER is openai -> anthropic -> xai -> deepseek;
    # anthropic has no key here, so it's skipped, not left as a gap.
    assert [t.provider for t in targets] == ["openai", "xai", "deepseek"]


def test_cascade_raises_no_llm_configured_when_nothing_is_available():
    with pytest.raises(NoLLMConfigured):
        resolver.llm_cascade_sync(ollama_up=False)


def test_cascade_with_pin_returns_single_target_no_fallback(monkeypatch):
    """A pin means exactly that provider, full stop — matches the pinned
    WEB_SEARCH_PROVIDER rule (app/search/web.py): a pin exists to force one
    specific backend for debugging, so it must never silently fall through."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-oa-test")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")  # must be ignored
    targets = resolver.llm_cascade_sync(ollama_up=True)  # ollama up must also be ignored
    assert [t.provider for t in targets] == ["openai"]


def test_cascade_with_pin_and_no_key_raises_immediately(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "xai")
    with pytest.raises(ModelUnavailable):
        resolver.llm_cascade_sync()


@pytest.mark.asyncio
async def test_async_cascade_matches_sync(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-oa-test")
    targets = await resolver.llm_cascade(ollama_up=False)
    assert [t.provider for t in targets] == ["openai"]


@pytest.mark.asyncio
async def test_async_resolution_matches_sync(monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "xai-test")
    target = await resolver.resolve_llm(ollama_up=False)
    assert target.provider == "xai"
    assert target.chat_model == settings.xai_chat_model


def test_embedding_auto_skips_chat_only_providers(monkeypatch):
    # Anthropic has no embeddings API: its key must NOT satisfy embeddings.
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(settings, "openai_api_key", "sk-oa-test")
    target = resolver.resolve_embedding_sync(ollama_up=False)
    assert target.provider == "openai"
    assert target.model == settings.openai_embedding_model


def test_embedding_auto_with_nothing_configured_gives_instructions():
    with pytest.raises(NoLLMConfigured) as exc_info:
        resolver.resolve_embedding_sync(ollama_up=False)
    message = str(exc_info.value.model)
    assert "OPENAI_API_KEY" in message
    assert "Ollama" in message


def test_embedding_choice_is_pinned_per_process(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-oa-test")
    first = resolver.resolve_embedding_sync(ollama_up=False)
    assert first.provider == "openai"
    # Ollama "coming back" mid-process must not flip the embedding backend.
    second = resolver.resolve_embedding_sync(ollama_up=True)
    assert second == first


def test_embedding_pinned_provider_uses_its_model(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-oa-test")
    target = resolver.resolve_embedding_sync()
    assert target.provider == "openai"
    assert target.model == settings.openai_embedding_model
    assert target.base_url == resolver.PROVIDER_BASE_URLS["openai"]
