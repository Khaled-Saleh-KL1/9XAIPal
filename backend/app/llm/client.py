"""Provider-agnostic LLM client.

Every chat call in the app goes through this module. With LLM_PROVIDER=auto
(the default), each of chat()/stream_chat()/chat_sync() below tries every
configured backend in priority order (app.llm.resolver.llm_cascade: Ollama
first if reachable, then every cloud provider with a key set, in order) and
falls through to the next on failure — the SAME messages get resent to the
next provider, never re-sent to the caller to retry itself. A pinned
LLM_PROVIDER still means exactly that one backend, no fallback.

⚠ **Streaming's fallback only covers pre-first-token failures.** Once even
one token has reached the caller (already rendered in the UI), silently
discarding it and restarting on a different provider would be a worse
experience than a clean error — so a stream that fails after starting to
answer raises, same as before this cascade existed. A stream that fails
before yielding anything (the common real case: a provider is down,
rate-limited, or misconfigured) falls through exactly like chat()/chat_sync()
do, with no visible effect on the caller at all.

Local Ollama uses its native API (keeps tag resolution and keep_alive
semantics); any cloud provider dispatches to its OpenAI-compatible endpoint.
OpenAI, Anthropic, xAI (Grok) and DeepSeek all expose the OpenAI
chat-completions protocol, so a single HTTP implementation covers all of
them; "custom" lets the user point LLM_BASE_URL at anything else that speaks
the same protocol (OpenRouter, vLLM, llama.cpp server, ...).

Callers say what KIND of call they make via ``role`` ("chat", "classifier",
"vlm") instead of naming a model, so the right model is used whichever
backend is active; an explicit ``model`` argument still overrides.

Message format: callers build Ollama-style messages (optional base64 `images`
list on a message). The OpenAI path converts those to content arrays with
data: URIs, so call sites never care which provider is active.
"""

import json
from typing import AsyncIterator, Optional

import httpx

from app.api.errors import ModelUnavailable
from app.core import circuit_breaker
from app.core.config import settings
from app.core.logging import get_logger
from app.llm import ollama_client, resolver
from app.llm.resolver import LLMTarget

logger = get_logger(__name__)

# Cloud inference can be slow on long prompts, but nowhere near local-26B slow.
_CLOUD_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=10.0)


def _headers(target: LLMTarget) -> dict:
    # The resolver guarantees a key for known cloud providers; "custom"
    # endpoints (local vLLM, llama.cpp) may legitimately run keyless.
    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    return headers


def _b64_mime(b64: str) -> str:
    """Sniff the image type from base64 magic-byte prefixes."""
    if b64.startswith("iVBOR"):
        return "image/png"
    if b64.startswith("/9j/"):
        return "image/jpeg"
    if b64.startswith("R0lGOD"):
        return "image/gif"
    if b64.startswith("UklGR"):
        return "image/webp"
    return "image/png"


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Convert Ollama-style messages (base64 `images` on the message) to the
    OpenAI content-array format with data: URIs."""
    out = []
    for m in messages:
        images = m.get("images")
        if not images:
            out.append({"role": m["role"], "content": m.get("content", "")})
            continue
        content: list[dict] = [{"type": "text", "text": m.get("content", "")}]
        for b64 in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{_b64_mime(b64)};base64,{b64}"},
            })
        out.append({"role": m["role"], "content": content})
    return out


def _is_reasoning_model(model: str) -> bool:
    """Heuristic: OpenAI reasoning model names start with 'o' + digit."""
    base = model.split("/")[-1]
    return len(base) >= 2 and base[0] == "o" and base[1].isdigit()


def _reasoning_effort_for(target: LLMTarget, resolved_model: str) -> Optional[str]:
    """Return a ``reasoning_effort`` value when cloud thinking mode is
    enabled and the resolved model looks like an OpenAI reasoning model."""
    if not settings.cloud_thinking_mode:
        return None
    if target.provider == "ollama":
        return None
    if not _is_reasoning_model(resolved_model):
        return None
    return "medium"


def _openai_payload(
    messages: list[dict],
    *,
    model: str,
    temperature: float,
    max_tokens: Optional[int],
    stream: bool,
    reasoning_effort: Optional[str] = None,
) -> dict:
    payload: dict = {
        "model": model,
        "messages": _to_openai_messages(messages),
        "temperature": temperature,
        "stream": stream,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Async chat (FastAPI request path)
# ─────────────────────────────────────────────────────────────────────────────

async def _chat_once(
    target: LLMTarget,
    messages: list[dict],
    *,
    resolved: str,
    temperature: float,
    num_predict: Optional[int],
    keep_alive: Optional[str],
) -> dict:
    """One provider, one attempt. Raises ModelUnavailable on any failure —
    never partially handled, so the cascade loop in chat() can always just
    catch-and-continue without guessing what state was left behind."""
    if target.provider == "ollama":
        return await ollama_client.chat(
            messages, model=resolved, temperature=temperature,
            num_predict=num_predict, keep_alive=keep_alive,
        )

    cap = num_predict if num_predict is not None else (settings.chat_num_predict or None)
    payload = _openai_payload(
        messages, model=resolved, temperature=temperature, max_tokens=cap, stream=False,
        reasoning_effort=_reasoning_effort_for(target, resolved),
    )
    url = f"{target.base_url}/chat/completions"
    async with httpx.AsyncClient(timeout=_CLOUD_TIMEOUT) as client:
        try:
            response = await client.post(url, json=payload, headers=_headers(target))
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise ModelUnavailable(f"{resolved} ({e.response.status_code}: {body})")
        except httpx.RequestError as e:
            raise ModelUnavailable(f"{resolved} (network error: {e})")
        data = response.json()

    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return {
        "content": (choice.get("message") or {}).get("content") or "",
        "model": data.get("model") or resolved,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


async def chat(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    role: str = "chat",
    temperature: float = 0.7,
    num_predict: Optional[int] = None,
    keep_alive: Optional[str] = None,
) -> dict:
    """Provider-dispatched chat completion. Same return shape as
    ollama_client.chat: {content, model, prompt_tokens, completion_tokens}.

    Tries every target in the cascade with the SAME messages, in order,
    until one succeeds. No partial output is possible here (unlike
    stream_chat), so a failure at any point is a clean, invisible-to-the-
    caller fall-through to the next provider.
    """
    targets = await resolver.llm_cascade()
    last_error: Optional[ModelUnavailable] = None
    for target in targets:
        resolved = model or target.model_for_role(role)
        try:
            result = await _chat_once(
                target, messages, resolved=resolved, temperature=temperature,
                num_predict=num_predict, keep_alive=keep_alive,
            )
        except ModelUnavailable as e:
            logger.warning(f"{target.provider} chat failed, falling through: {e}")
            circuit_breaker.record_failure(target.provider)
            last_error = e
            continue
        circuit_breaker.record_success(target.provider)
        return result
    raise last_error or ModelUnavailable("no LLM provider configured")


async def _stream_once(
    target: LLMTarget,
    messages: list[dict],
    *,
    resolved: str,
    temperature: float,
    num_predict: Optional[int],
    keep_alive: Optional[str],
) -> AsyncIterator[dict]:
    """One provider, one streaming attempt. Raises ModelUnavailable on
    failure at any point — stream_chat() below decides whether that's safe
    to retry based on whether it had already forwarded a token to ITS
    caller when the exception arrived."""
    if target.provider == "ollama":
        async for event in ollama_client.stream_chat(
            messages, model=resolved, temperature=temperature,
            num_predict=num_predict, keep_alive=keep_alive,
        ):
            yield event
        return

    cap = num_predict if num_predict is not None else (settings.chat_num_predict or None)
    payload = _openai_payload(
        messages, model=resolved, temperature=temperature, max_tokens=cap, stream=True,
        reasoning_effort=_reasoning_effort_for(target, resolved),
    )
    url = f"{target.base_url}/chat/completions"
    content_parts: list[str] = []
    final_model = resolved
    async with httpx.AsyncClient(timeout=_CLOUD_TIMEOUT) as client:
        try:
            async with client.stream("POST", url, json=payload, headers=_headers(target)) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:500]
                    raise ModelUnavailable(f"{resolved} ({response.status_code}: {body})")
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except ValueError:
                        continue
                    final_model = data.get("model") or final_model
                    delta = ((data.get("choices") or [{}])[0].get("delta")) or {}
                    token = delta.get("content") or ""
                    if token:
                        content_parts.append(token)
                        yield {"type": "token", "text": token}
        except httpx.RequestError as e:
            raise ModelUnavailable(f"{resolved} (network error: {e})")

    yield {
        "type": "done",
        "content": "".join(content_parts),
        "model": final_model,
        "prompt_tokens": None,
        "completion_tokens": None,
    }


async def stream_chat(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    role: str = "chat",
    temperature: float = 0.7,
    num_predict: Optional[int] = None,
    keep_alive: Optional[str] = None,
) -> AsyncIterator[dict]:
    """Provider-dispatched streaming chat.

    Yields ``{"type": "token", "text": ...}`` per token, then a final
    ``{"type": "done", "content", "model", "prompt_tokens", "completion_tokens"}``.

    Falls through to the next cascade target on a failure that happens
    BEFORE any token reached the caller — a dead/misconfigured/rate-limited
    provider is invisible to whoever is rendering the stream. Once a token
    has gone out, a later failure raises instead of silently restarting:
    discarding visible partial output and resuming on a different provider
    (possibly a different model, different voice, duplicated content) is a
    worse experience than a clean error the caller can show and retry.
    """
    targets = await resolver.llm_cascade()
    last_error: Optional[ModelUnavailable] = None
    for target in targets:
        resolved = model or target.model_for_role(role)
        yielded_any = False
        try:
            async for event in _stream_once(
                target, messages, resolved=resolved, temperature=temperature,
                num_predict=num_predict, keep_alive=keep_alive,
            ):
                yielded_any = True
                yield event
            circuit_breaker.record_success(target.provider)
            return
        except ModelUnavailable as e:
            circuit_breaker.record_failure(target.provider)
            if yielded_any:
                raise
            logger.warning(f"{target.provider} stream failed before any output, falling through: {e}")
            last_error = e
    raise last_error or ModelUnavailable("no LLM provider configured")


async def is_available() -> bool:
    """Whether at least one cascade target answers right now (used by /health)."""
    try:
        targets = await resolver.llm_cascade()
    except ModelUnavailable:
        return False
    for target in targets:
        try:
            if target.provider == "ollama":
                if await ollama_client.is_available():
                    return True
                continue
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{target.base_url}/models", headers=_headers(target))
                if resp.status_code == 200:
                    return True
        except Exception:
            continue
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Sync chat (Celery workers: summaries, figure descriptions)
# ─────────────────────────────────────────────────────────────────────────────

def _chat_sync_once(
    target: LLMTarget,
    messages: list[dict],
    *,
    resolved: str,
    temperature: float,
    images: Optional[list[str]],
) -> dict:
    """One provider, one attempt. Raises ModelUnavailable on any failure."""
    if target.provider == "ollama":
        return ollama_client.chat_sync(
            messages, model=resolved, temperature=temperature, images=images,
        )

    final_messages = list(messages)
    if images and final_messages and final_messages[-1].get("role") == "user":
        final_messages[-1] = {**final_messages[-1], "images": images}

    payload = _openai_payload(
        final_messages, model=resolved, temperature=temperature, max_tokens=None, stream=False,
        reasoning_effort=_reasoning_effort_for(target, resolved),
    )
    url = f"{target.base_url}/chat/completions"
    with httpx.Client(timeout=300.0) as client:
        try:
            response = client.post(url, json=payload, headers=_headers(target))
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise ModelUnavailable(f"{resolved} ({e.response.status_code}: {body})")
        except httpx.RequestError as e:
            raise ModelUnavailable(f"{resolved} (network error: {e})")
        data = response.json()

    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return {
        "content": (choice.get("message") or {}).get("content") or "",
        "model": data.get("model") or resolved,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def chat_sync(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    role: str = "chat",
    temperature: float = 0.3,
    images: Optional[list[str]] = None,
) -> dict:
    """Provider-dispatched synchronous chat for Celery workers.

    Same cascade as chat(): tries every configured target with the SAME
    messages until one succeeds. No streaming here, so no partial-output
    caveat — every failure is a clean fall-through.
    """
    targets = resolver.llm_cascade_sync()
    last_error: Optional[ModelUnavailable] = None
    for target in targets:
        resolved = model or target.model_for_role(role)
        try:
            result = _chat_sync_once(
                target, messages, resolved=resolved, temperature=temperature, images=images,
            )
        except ModelUnavailable as e:
            logger.warning(f"[sync] {target.provider} chat failed, falling through: {e}")
            circuit_breaker.record_failure(target.provider)
            last_error = e
            continue
        circuit_breaker.record_success(target.provider)
        return result
    raise last_error or ModelUnavailable("no LLM provider configured")
