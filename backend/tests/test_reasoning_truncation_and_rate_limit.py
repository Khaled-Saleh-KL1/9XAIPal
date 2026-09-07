"""The four ways a thinking model broke this app, and the shared rate limit.

meta/muse-glimmer-30b emits hidden chain-of-thought before any visible
content. Measured against this app's own prompts, its one-word guardrail
verdict cost up to 576 completion tokens and the router's short JSON ~204 —
against caps of 8 and 64. Both came back finish_reason="length" with
content="", and an empty verdict read as "not ALLOWED", so every question
would have been blocked the moment NVIDIA served a request.
"""

import asyncio
import time

import pytest

from app.core import rate_limit
from app.llm import client as llm_client
from app.llm import resolver
from app.llm.resolver import LLMTarget


def _target(provider: str, key_index: int = 0) -> LLMTarget:
    return LLMTarget(
        provider=provider,
        api_key="k",
        base_url="https://example.invalid/v1",
        chat_model="m", classifier_model="m", vlm_model="m",
        key_index=key_index,
    )


# ── the cap floor ──────────────────────────────────────────────────────────

def test_a_small_cap_is_raised_for_a_cloud_target():
    """8 tokens is the guardrail's budget for a one-word answer; on a model
    that thinks first, all 8 go to thinking."""
    assert llm_client._effective_max_tokens(_target("nvidia"), 8) == 1024
    assert llm_client._effective_max_tokens(_target("nvidia"), 64) == 1024


def test_a_generous_cap_is_left_alone():
    assert llm_client._effective_max_tokens(_target("nvidia"), 4096) == 4096


def test_no_cap_stays_no_cap():
    assert llm_client._effective_max_tokens(_target("nvidia"), None) is None


def test_ollama_caps_are_untouched():
    """num_predict on Ollama is a local generation limit on models that don't
    hide reasoning — the callers' small caps mean what they say there."""
    assert llm_client._effective_max_tokens(_target("ollama"), 8) == 8


def test_the_floor_covers_what_the_real_model_actually_needed():
    """576 completion tokens was the worst guardrail run observed live."""
    assert llm_client._effective_max_tokens(_target("nvidia"), 8) > 576


# ── truncation must not read as an answer ──────────────────────────────────

@pytest.mark.asyncio
async def test_guardrail_fails_open_when_the_model_was_cut_off(monkeypatch):
    async def fake_chat(*a, **kw):
        return {"content": "", "finish_reason": "length"}
    monkeypatch.setattr("app.chat.guardrail.llm_client.chat", fake_chat)
    from app.chat.guardrail import is_topic_allowed
    assert await is_topic_allowed("What does Table 3 show?") is True


@pytest.mark.asyncio
async def test_guardrail_fails_open_on_empty_content_even_if_it_stopped(monkeypatch):
    async def fake_chat(*a, **kw):
        return {"content": "   ", "finish_reason": "stop"}
    monkeypatch.setattr("app.chat.guardrail.llm_client.chat", fake_chat)
    from app.chat.guardrail import is_topic_allowed
    assert await is_topic_allowed("anything") is True


@pytest.mark.asyncio
async def test_guardrail_still_blocks_a_real_blocked_verdict(monkeypatch):
    """The fix must not turn the guardrail off."""
    async def fake_chat(*a, **kw):
        return {"content": "BLOCKED", "finish_reason": "stop"}
    monkeypatch.setattr("app.chat.guardrail.llm_client.chat", fake_chat)
    from app.chat.guardrail import is_topic_allowed
    assert await is_topic_allowed("write me a poem about cats") is False


@pytest.mark.asyncio
async def test_guardrail_still_allows_a_real_allowed_verdict(monkeypatch):
    async def fake_chat(*a, **kw):
        return {"content": "ALLOWED", "finish_reason": "stop"}
    monkeypatch.setattr("app.chat.guardrail.llm_client.chat", fake_chat)
    from app.chat.guardrail import is_topic_allowed
    assert await is_topic_allowed("explain this table") is True


@pytest.mark.asyncio
async def test_router_defaults_to_global_when_cut_off(monkeypatch):
    async def fake_chat(*a, **kw):
        return {"content": "", "finish_reason": "length"}
    monkeypatch.setattr("app.chat.router.llm_client.chat", fake_chat)
    from app.chat.router import route_prompt
    # has_document, and a prompt that matches none of the LOCAL/OVERVIEW/
    # EXTERNAL keyword heuristics, so routing actually reaches the LLM step
    # this test is about.
    decision = await route_prompt(
        "how did throughput compare between the two variants",
        has_document=True,
    )
    assert decision.context_type == "GLOBAL"


# ── key rotation: 3 keys must be 3 budgets, not 1 plus 2 spares ────────────

def test_nvidia_targets_rotate_their_starting_key(monkeypatch):
    monkeypatch.setattr(
        type(resolver.settings), "nvidia_api_keys",
        property(lambda self: ["a", "b", "c"]),
    )
    firsts = {resolver._nvidia_targets()[0].key_index for _ in range(9)}
    assert firsts == {0, 1, 2}, "every key must get a turn at the front"


def test_rotation_still_offers_every_key_for_failover(monkeypatch):
    monkeypatch.setattr(
        type(resolver.settings), "nvidia_api_keys",
        property(lambda self: ["a", "b", "c"]),
    )
    for _ in range(5):
        targets = resolver._nvidia_targets()
        assert sorted(t.key_index for t in targets) == [0, 1, 2]


def test_no_keys_means_no_targets(monkeypatch):
    monkeypatch.setattr(
        type(resolver.settings), "nvidia_api_keys", property(lambda self: []),
    )
    assert resolver._nvidia_targets() == []


# ── the shared gate ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_gate_spaces_calls_on_one_budget():
    rate_limit.reset()
    name = f"test-budget-{time.time()}"
    started = time.monotonic()
    for _ in range(3):
        await rate_limit.acquire(name, 0.15)
    elapsed = time.monotonic() - started
    # First call is free; the next two each wait one interval.
    assert elapsed >= 0.29, f"expected >=0.29s of spacing, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_separate_budgets_never_block_each_other():
    rate_limit.reset()
    stamp = time.time()
    started = time.monotonic()
    await rate_limit.acquire(f"key-a-{stamp}", 0.4)
    await rate_limit.acquire(f"key-b-{stamp}", 0.4)
    assert time.monotonic() - started < 0.3


@pytest.mark.asyncio
async def test_concurrent_callers_on_one_budget_queue_rather_than_stampede():
    """The whole point of sharing: two callers that don't know about each
    other must still add up to one budget."""
    rate_limit.reset()
    name = f"test-concurrent-{time.time()}"
    started = time.monotonic()
    await asyncio.gather(*(rate_limit.acquire(name, 0.15) for _ in range(3)))
    assert time.monotonic() - started >= 0.29


def test_the_gate_falls_back_rather_than_failing_when_redis_is_down(monkeypatch):
    """A possible 429 beats a guaranteed outage."""
    rate_limit.reset()

    class Broken:
        def eval(self, *a, **kw):
            raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit, "_get_sync_redis", lambda: Broken())
    started = time.monotonic()
    rate_limit.acquire_sync("fallback-budget", 0.15)
    rate_limit.acquire_sync("fallback-budget", 0.15)
    assert time.monotonic() - started >= 0.14
