"""Backend auto-detection: which AI provider(s) answer this process's calls.

With LLM_PROVIDER=auto (the default), chat calls go through a cascade, not a
single pick — see llm_cascade()/llm_cascade_sync() below, consumed by
app.llm.client's chat()/stream_chat()/chat_sync(). Priority order:

1. Ollama — if it answers at OLLAMA_BASE_URL, tried first with the CHAT_MODEL
   / VLM_MODEL / CLASSIFIER_MODEL / EMBEDDING_MODEL configured in .env.
2. Cloud APIs, one by one, every one with an API key in .env, in order:
   openai → anthropic → xai → deepseek. Each uses its own
   <PROVIDER>_CHAT_MODEL / <PROVIDER>_EMBEDDING_MODEL setting, never the
   Ollama model names.
3. Nothing configured → NoLLMConfigured, whose message tells the user exactly
   what to put in backend/.env.

Ollama passing its cheap reachability probe doesn't guarantee the real chat
call succeeds (wrong model pulled, OOM, a crash mid-generation) — client.py's
retry loop catches that too and falls through to the next configured
provider, the same "try each, fall through on failure" shape the web search
cascade uses (app/search/web.py).

Setting LLM_PROVIDER / EMBEDDING_PROVIDER explicitly pins a backend and skips
both the probing and the cascade — a pin means exactly that one provider, no
fallback, same rule as a pinned WEB_SEARCH_PROVIDER. The Ollama reachability
probe is cached briefly so the chain doesn't add a round trip to every model
call; the embedding choice is pinned for the process lifetime so one library
is never embedded by two different models within a run (embeddings have no
cascade — a mid-run backend switch would produce vectors the rest of the
library isn't comparable against).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx

from app.api.errors import ModelUnavailable, NoLLMConfigured
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Default API base URLs per cloud provider. Anthropic is its official
# OpenAI-compatibility endpoint. LLM_BASE_URL / EMBEDDING_BASE_URL override
# these.
PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
}

# Auto-detection order for chat. For embeddings only OpenAI applies.
CLOUD_PROVIDER_ORDER = ["openai", "anthropic", "xai", "deepseek"]
EMBEDDING_CLOUD_ORDER = ["openai"]

NO_LLM_MESSAGE = (
    "No AI backend is configured. Put your API key or your Ollama connection in "
    "backend/.env: either start Ollama (OLLAMA_BASE_URL is {ollama_url}) or set "
    "one of OPENAI_API_KEY, ANTHROPIC_API_KEY, XAI_API_KEY, "
    "DEEPSEEK_API_KEY, then restart the backend."
)

NO_EMBEDDING_MESSAGE = (
    "No embedding backend is configured. Put your API key or your Ollama "
    "connection in backend/.env: either start Ollama (OLLAMA_BASE_URL is "
    "{ollama_url}) or set OPENAI_API_KEY — Anthropic, xAI and "
    "DeepSeek don't offer embedding APIs — then restart the backend."
)


@dataclass(frozen=True)
class LLMTarget:
    provider: str          # "ollama" | "openai" | ... | "custom"
    api_key: str           # "" for local ollama / optionally for custom
    base_url: str          # OpenAI-compatible base, or OLLAMA_BASE_URL for ollama
    chat_model: str
    classifier_model: str
    vlm_model: str

    def model_for_role(self, role: str) -> str:
        if role == "classifier":
            return self.classifier_model
        if role == "vlm":
            return self.vlm_model
        return self.chat_model


@dataclass(frozen=True)
class EmbeddingTarget:
    provider: str
    api_key: str
    base_url: str
    model: str


# ─────────────────────────────────────────────────────────────────────────────
# Ollama reachability probe (short timeout, briefly cached)
# ─────────────────────────────────────────────────────────────────────────────

_PROBE_TTL_SECONDS = 30.0
_probe_cache: dict[str, tuple[float, bool]] = {}


def _probe_cached(url: str) -> Optional[bool]:
    entry = _probe_cache.get(url)
    if entry and (time.monotonic() - entry[0]) < _PROBE_TTL_SECONDS:
        return entry[1]
    return None


def _probe_store(url: str, up: bool) -> None:
    _probe_cache[url] = (time.monotonic(), up)


def _ollama_headers() -> dict:
    """Headers for native Ollama endpoints; adds Bearer auth when a key is set."""
    headers = {}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    return headers


def ollama_reachable_sync() -> bool:
    url = settings.ollama_base_url
    cached = _probe_cached(url)
    if cached is not None:
        return cached
    try:
        with httpx.Client(timeout=3.0) as client:
            up = client.get(f"{url}/api/tags", headers=_ollama_headers()).status_code == 200
    except Exception:
        up = False
    _probe_store(url, up)
    return up


async def ollama_reachable() -> bool:
    url = settings.ollama_base_url
    cached = _probe_cached(url)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            up = (await client.get(f"{url}/api/tags", headers=_ollama_headers())).status_code == 200
    except Exception:
        up = False
    _probe_store(url, up)
    return up


# ─────────────────────────────────────────────────────────────────────────────
# Chat resolution
# ─────────────────────────────────────────────────────────────────────────────

def cloud_api_key(provider: str) -> str:
    return getattr(settings, f"{provider}_api_key", "") or ""


def _explicit_llm_provider() -> Optional[str]:
    p = (settings.llm_provider or "auto").strip().lower()
    return None if p in ("", "auto") else p


def _ollama_target() -> LLMTarget:
    return LLMTarget(
        provider="ollama",
        api_key=settings.ollama_api_key,
        base_url=settings.ollama_base_url,
        chat_model=settings.chat_model,
        classifier_model=settings.effective_classifier_model,
        vlm_model=settings.effective_vlm_model,
    )


def _cloud_llm_target(provider: str) -> LLMTarget:
    if settings.llm_base_url:
        base_url = settings.llm_base_url.rstrip("/")
    else:
        base_url = PROVIDER_BASE_URLS.get(provider, "")
        if not base_url:
            raise ModelUnavailable(
                f"LLM_PROVIDER '{provider}' is not recognized and no LLM_BASE_URL "
                f"override is set. Known providers: auto, ollama, "
                f"{', '.join(PROVIDER_BASE_URLS)}, custom"
            )

    api_key = cloud_api_key(provider) or settings.llm_api_key
    if not api_key and provider in PROVIDER_BASE_URLS:
        raise ModelUnavailable(
            f"LLM_PROVIDER is '{provider}' but {provider.upper()}_API_KEY "
            f"(or LLM_API_KEY) is not set in backend/.env"
        )

    # "custom" serves whatever the user's endpoint hosts → CHAT_MODEL applies.
    chat_model = getattr(settings, f"{provider}_chat_model", "") or settings.chat_model
    return LLMTarget(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        chat_model=chat_model,
        classifier_model=chat_model,
        vlm_model=chat_model,
    )


def _first_cloud_llm() -> Optional[LLMTarget]:
    for provider in CLOUD_PROVIDER_ORDER:
        if cloud_api_key(provider):
            return _cloud_llm_target(provider)
    return None


_last_logged_llm: Optional[str] = None


def _log_llm_choice(target: LLMTarget) -> None:
    """Log the active backend once (and again whenever it changes)."""
    global _last_logged_llm
    signature = f"{target.provider}|{target.base_url}|{target.chat_model}"
    if signature == _last_logged_llm:
        return
    _last_logged_llm = signature
    if target.provider == "ollama":
        logger.info("LLM backend: ollama @ %s (chat model: %s)", target.base_url, target.chat_model)
    else:
        logger.info("LLM backend: %s (chat model: %s)", target.provider, target.chat_model)


def resolve_llm_sync(ollama_up: Optional[bool] = None) -> LLMTarget:
    """Pick the chat backend. ``ollama_up`` injects the probe result (tests)."""
    explicit = _explicit_llm_provider()
    if explicit == "ollama":
        target = _ollama_target()
    elif explicit:
        target = _cloud_llm_target(explicit)
    else:
        up = ollama_reachable_sync() if ollama_up is None else ollama_up
        if up:
            target = _ollama_target()
        else:
            cloud = _first_cloud_llm()
            if cloud is None:
                raise NoLLMConfigured(NO_LLM_MESSAGE.format(ollama_url=settings.ollama_base_url))
            target = cloud
    _log_llm_choice(target)
    return target


async def resolve_llm(ollama_up: Optional[bool] = None) -> LLMTarget:
    """Async variant of resolve_llm_sync (uses the async Ollama probe)."""
    explicit = _explicit_llm_provider()
    if explicit is not None or ollama_up is not None:
        return resolve_llm_sync(ollama_up=ollama_up)
    return resolve_llm_sync(ollama_up=await ollama_reachable())


# ─────────────────────────────────────────────────────────────────────────────
# Chat cascade: every target "auto" would try, in order
# ─────────────────────────────────────────────────────────────────────────────
#
# resolve_llm() above picks ONE target and stops — today, if Ollama passes its
# cheap /api/tags probe but the real completion call then fails (wrong model
# pulled, OOM, mid-generation crash), the whole request dies even when a cloud
# key is configured and would have answered. app.llm.client wraps the actual
# chat/stream_chat/chat_sync calls in a retry loop over this list instead: the
# probe still avoids paying a full connect-timeout on every request when
# Ollama is simply not running, but a REAL failure past that point now falls
# through to the next provider — the same "try each, fall through on failure"
# shape as the web search cascade (app/search/web.py).


def llm_cascade_sync(ollama_up: Optional[bool] = None) -> list[LLMTarget]:
    """Every target to try, in priority order, for THIS call.

    An explicit LLM_PROVIDER pin returns exactly that one target (or raises,
    via resolve_llm_sync's existing validation) — no fallback, matching how a
    pinned web search provider also skips the cascade. This is deliberate:
    a pin exists to force one specific backend for debugging, and silently
    falling through would defeat that.
    """
    explicit = _explicit_llm_provider()
    if explicit:
        return [resolve_llm_sync(ollama_up=ollama_up)]
    up = ollama_reachable_sync() if ollama_up is None else ollama_up
    targets: list[LLMTarget] = []
    if up:
        targets.append(_ollama_target())
    for provider in CLOUD_PROVIDER_ORDER:
        if cloud_api_key(provider):
            targets.append(_cloud_llm_target(provider))
    if not targets:
        raise NoLLMConfigured(NO_LLM_MESSAGE.format(ollama_url=settings.ollama_base_url))
    return targets


async def llm_cascade(ollama_up: Optional[bool] = None) -> list[LLMTarget]:
    """Async variant of llm_cascade_sync (uses the async Ollama probe)."""
    explicit = _explicit_llm_provider()
    if explicit is not None or ollama_up is not None:
        return llm_cascade_sync(ollama_up=ollama_up)
    return llm_cascade_sync(ollama_up=await ollama_reachable())


# ─────────────────────────────────────────────────────────────────────────────
# Embedding resolution (pinned per process)
# ─────────────────────────────────────────────────────────────────────────────

def _explicit_embedding_provider() -> Optional[str]:
    p = (settings.embedding_provider or "auto").strip().lower()
    return None if p in ("", "auto") else p


def _ollama_embedding_target() -> EmbeddingTarget:
    return EmbeddingTarget(
        provider="ollama",
        api_key=settings.ollama_api_key,
        base_url=settings.ollama_base_url,
        model=settings.embedding_model,
    )


def _cloud_embedding_target(provider: str) -> EmbeddingTarget:
    if settings.embedding_base_url:
        base_url = settings.embedding_base_url.rstrip("/")
    elif settings.llm_base_url and provider == (settings.llm_provider or "").strip().lower():
        base_url = settings.llm_base_url.rstrip("/")
    else:
        base_url = PROVIDER_BASE_URLS.get(provider, "")
        if not base_url:
            raise ModelUnavailable(
                f"EMBEDDING_PROVIDER '{provider}' is not recognized and no "
                f"EMBEDDING_BASE_URL override is set"
            )

    api_key = settings.embedding_api_key or cloud_api_key(provider) or settings.llm_api_key
    if not api_key and provider in PROVIDER_BASE_URLS:
        raise ModelUnavailable(
            f"EMBEDDING_PROVIDER is '{provider}' but {provider.upper()}_API_KEY "
            f"(or EMBEDDING_API_KEY / LLM_API_KEY) is not set in backend/.env"
        )

    model = getattr(settings, f"{provider}_embedding_model", "") or settings.embedding_model
    return EmbeddingTarget(provider=provider, api_key=api_key, base_url=base_url, model=model)


# Embeddings are pinned for the process lifetime: vectors from different
# models are not comparable, so a mid-run Ollama hiccup must not silently
# switch a half-embedded library to a cloud model. Only successful
# resolutions are pinned — a process started before anything is configured
# recovers as soon as a backend appears.
_pinned_embedding: Optional[EmbeddingTarget] = None


def _resolve_embedding_unpinned(up: bool) -> EmbeddingTarget:
    if up:
        return _ollama_embedding_target()
    for provider in EMBEDDING_CLOUD_ORDER:
        if cloud_api_key(provider):
            return _cloud_embedding_target(provider)
    raise NoLLMConfigured(NO_EMBEDDING_MESSAGE.format(ollama_url=settings.ollama_base_url))


def resolve_embedding_sync(ollama_up: Optional[bool] = None) -> EmbeddingTarget:
    """Pick the embedding backend. ``ollama_up`` injects the probe (tests)."""
    global _pinned_embedding
    explicit = _explicit_embedding_provider()
    if explicit == "ollama":
        return _ollama_embedding_target()
    if explicit:
        return _cloud_embedding_target(explicit)
    if _pinned_embedding is not None:
        return _pinned_embedding
    up = ollama_reachable_sync() if ollama_up is None else ollama_up
    target = _resolve_embedding_unpinned(up)
    _pinned_embedding = target
    logger.info("Embedding backend: %s (model: %s)", target.provider, target.model)
    return target


async def resolve_embedding(ollama_up: Optional[bool] = None) -> EmbeddingTarget:
    """Async variant of resolve_embedding_sync (uses the async Ollama probe)."""
    explicit = _explicit_embedding_provider()
    if explicit is not None or _pinned_embedding is not None or ollama_up is not None:
        return resolve_embedding_sync(ollama_up=ollama_up)
    return resolve_embedding_sync(ollama_up=await ollama_reachable())


def reset_resolution_cache() -> None:
    """Clear probe cache, embedding pin and choice log (tests / reconfigure)."""
    global _pinned_embedding, _last_logged_llm
    _probe_cache.clear()
    _pinned_embedding = None
    _last_logged_llm = None
