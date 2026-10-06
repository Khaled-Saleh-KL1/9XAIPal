import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import count

import httpx
import pytest
import redis

from app.core.config import settings
from app.llm import availability, resolver
from app.workers import reliability


OLLAMA_KEY = "m4-test-ollama-key"
NVIDIA_KEY_A = "m4-test-nvidia-key-a"
NVIDIA_KEY_B = "m4-test-nvidia-key-b"


@pytest.fixture
def real_redis(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "")
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    client.ping()
    for pattern in (
        "llm:availability:v1:*",
        "llm:model-availability-refresh:*",
        "ratelimit:next_free:nvidia#*",
    ):
        keys = list(client.scan_iter(match=pattern))
        if keys:
            client.delete(*keys)
    yield client
    for pattern in (
        "llm:availability:v1:*",
        "llm:model-availability-refresh:*",
        "ratelimit:next_free:nvidia#*",
    ):
        keys = list(client.scan_iter(match=pattern))
        if keys:
            client.delete(*keys)
    client.close()


def _install_mock_http(monkeypatch, handler):
    real_async_client = httpx.AsyncClient

    def mock_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_async_client)


def _tags(*models):
    return {"models": [{"name": model, "size": 1024} for model in models]}


def _cache_value(real_redis, provider, model):
    value = real_redis.get(availability._key(provider, model))
    return json.loads(value) if value is not None else None


def test_refresh_records_402_with_the_existing_unavailable_reason(
    monkeypatch, real_redis, caplog
):
    model = "m4-probe-402"
    requests = []
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", OLLAMA_KEY)

    async def handler(request):
        requests.append(request)
        if request.url.path == "/api/tags":
            assert request.headers["Authorization"] == f"Bearer {OLLAMA_KEY}"
            return httpx.Response(200, json=_tags(model))
        body = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert request.headers["Authorization"] == f"Bearer {OLLAMA_KEY}"
        assert body == {
            "model": model,
            "messages": [{"role": "user", "content": "Say OK"}],
            "stream": False,
            "options": {"num_predict": 4},
        }
        return httpx.Response(402)

    _install_mock_http(monkeypatch, handler)
    caplog.set_level(logging.INFO, logger="app.workers.reliability")

    result = reliability.refresh_model_availability.run()

    assert result == {"available": 0, "unavailable": 1, "skipped": 0}
    assert _cache_value(real_redis, "ollama", model) == {
        "available": False,
        "unavailable_reason": "needs a paid Ollama plan",
    }
    assert len(requests) == 2
    assert "available=0 unavailable=1 skipped=0" in caplog.text
    assert OLLAMA_KEY not in caplog.text
    summaries = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Model availability refresh:")
    ]
    assert summaries == ["Model availability refresh: available=0 unavailable=1 skipped=0"]


def test_refresh_records_http_200_as_available(monkeypatch, real_redis):
    model = "m4-probe-200"
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", "")

    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=_tags(model))
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json={"message": {"content": "OK"}})

    _install_mock_http(monkeypatch, handler)

    result = reliability.refresh_model_availability.run()

    assert result == {"available": 1, "unavailable": 0, "skipped": 0}
    assert _cache_value(real_redis, "ollama", model) == {"available": True}


@pytest.mark.parametrize("failure", ["server-error", "rate-limit", "timeout"])
def test_refresh_does_not_record_transient_http_failure_or_timeout(
    monkeypatch, real_redis, failure
):
    model = f"m4-probe-{failure}"
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    if failure == "timeout":
        monkeypatch.setattr(reliability, "MODEL_PROBE_TIMEOUT_SECONDS", 0.01)

    cached_failure = {
        "available": False,
        "unavailable_reason": "model not found",
    }
    cached_raw = json.dumps(cached_failure, separators=(",", ":"))
    cache_key = availability._key("ollama", model)
    real_redis.set(cache_key, cached_raw, px=45_000)
    ttl_before = real_redis.pttl(cache_key)

    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=_tags(model))
        if failure == "timeout":
            await asyncio.sleep(0.05)
            return httpx.Response(200)
        return httpx.Response(429 if failure == "rate-limit" else 500)

    _install_mock_http(monkeypatch, handler)

    result = reliability.refresh_model_availability.run()

    assert result == {"available": 0, "unavailable": 0, "skipped": 1}
    assert real_redis.get(cache_key) == cached_raw
    ttl_after = real_redis.pttl(cache_key)
    assert 0 < ttl_after <= ttl_before
    assert ttl_before - ttl_after < 1_000


def test_refresh_discovers_ollama_chat_models_and_provider_pins(monkeypatch):
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "nvidia_api_key", NVIDIA_KEY_A)

    async def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json=_tags(
                "m4-discovery-chat",
                "m4-discovery-embedding",
                "meta/muse-glimmer-30b",
            ),
        )

    _install_mock_http(monkeypatch, handler)

    models = asyncio.run(reliability._model_availability_probe_targets())

    assert models == [
        ("ollama", "m4-discovery-chat"),
        ("nvidia", "meta/muse-glimmer-30b"),
    ]


def test_nvidia_probe_uses_rotated_key_and_shared_rate_limiter(
    monkeypatch, real_redis, caplog
):
    model = "meta/muse-glimmer-30b"
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", f"{NVIDIA_KEY_A},{NVIDIA_KEY_B}")
    monkeypatch.setattr(resolver, "_nvidia_rotation", count(1))
    caplog.set_level(logging.INFO, logger="app.workers.reliability")

    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=_tags())
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {NVIDIA_KEY_B}"
        assert body == {
            "model": model,
            "messages": [{"role": "user", "content": "Say OK"}],
            "stream": False,
            "max_tokens": 8,
        }
        return httpx.Response(200, json={"choices": []})

    _install_mock_http(monkeypatch, handler)

    result = reliability.refresh_model_availability.run()

    assert result == {"available": 1, "unavailable": 0, "skipped": 0}
    assert _cache_value(real_redis, "nvidia", model) == {"available": True}
    assert NVIDIA_KEY_A not in caplog.text
    assert NVIDIA_KEY_B not in caplog.text
    assert real_redis.exists("ratelimit:next_free:nvidia#1")


def test_unconfigured_nvidia_pin_is_not_probed_or_counted(monkeypatch, real_redis):
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "")
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=_tags())
        raise AssertionError(f"unexpected provider request: {request.url.path}")

    _install_mock_http(monkeypatch, handler)

    result = reliability.refresh_model_availability.run()

    assert result == {"available": 0, "unavailable": 0, "skipped": 0}
    assert [request.url.path for request in requests] == ["/api/tags"]
    assert _cache_value(real_redis, "nvidia", "meta/muse-glimmer-30b") is None


@pytest.mark.parametrize(
    "tags_failure", ["http-error", "malformed-json", "malformed-payload"]
)
def test_ollama_tags_failure_does_not_prevent_configured_provider_pin_probe(
    monkeypatch, real_redis, caplog, tags_failure
):
    model = "meta/muse-glimmer-30b"
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", NVIDIA_KEY_A)
    monkeypatch.setattr(resolver, "_nvidia_rotation", count(0))
    caplog.set_level(logging.INFO, logger="app.workers.reliability")

    async def handler(request):
        if request.url.path == "/api/tags":
            if tags_failure == "http-error":
                return httpx.Response(503)
            if tags_failure == "malformed-json":
                return httpx.Response(200, content=b"not-json")
            return httpx.Response(200, json={"models": {"name": "not-a-list"}})
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {NVIDIA_KEY_A}"
        return httpx.Response(200)

    _install_mock_http(monkeypatch, handler)

    result = reliability.refresh_model_availability.run()

    assert result == {"available": 1, "unavailable": 0, "skipped": 0}
    assert _cache_value(real_redis, "nvidia", model) == {"available": True}
    assert NVIDIA_KEY_A not in caplog.text


def test_repeated_refresh_honors_the_shared_nvidia_rate_limit(
    monkeypatch, real_redis, caplog
):
    model = "meta/muse-glimmer-30b"
    rate_limit_key = "ratelimit:next_free:nvidia#0"
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", NVIDIA_KEY_A)
    monkeypatch.setattr(resolver, "_nvidia_rotation", count(0))
    caplog.set_level(logging.WARNING, logger="app.core.rate_limit")

    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=_tags())
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": []})

    _install_mock_http(monkeypatch, handler)

    first_result = reliability.refresh_model_availability.run()
    first_reservation = float(real_redis.get(rate_limit_key))
    second_started = time.monotonic()
    second_result = reliability.refresh_model_availability.run()
    second_elapsed = time.monotonic() - second_started
    second_reservation = float(real_redis.get(rate_limit_key))

    assert first_result == {"available": 1, "unavailable": 0, "skipped": 0}
    assert second_result == first_result
    assert second_elapsed >= 1.3
    assert second_reservation > first_reservation + 1.4
    assert "Redis unavailable" not in caplog.text


def test_refresh_never_runs_more_than_three_model_requests_concurrently(
    monkeypatch, real_redis
):
    models = [f"m4-concurrent-{index}" for index in range(8)]
    active = 0
    max_active = 0

    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", "")

    async def handler(request):
        nonlocal active, max_active
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=_tags(*models))
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200)

    _install_mock_http(monkeypatch, handler)

    result = reliability.refresh_model_availability.run()

    assert result == {"available": 8, "unavailable": 0, "skipped": 0}
    assert max_active <= 3


def test_redis_lock_skips_a_second_overlapping_refresh(monkeypatch, real_redis):
    model = "m4-probe-overlap"
    started = threading.Event()
    release = threading.Event()
    tags_calls = 0
    post_calls = 0
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_api_key", "")

    async def handler(request):
        nonlocal tags_calls, post_calls
        if request.url.path == "/api/tags":
            tags_calls += 1
            return httpx.Response(200, json=_tags(model))
        post_calls += 1
        started.set()
        await asyncio.to_thread(release.wait, 5)
        return httpx.Response(200)

    _install_mock_http(monkeypatch, handler)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first_run = executor.submit(reliability.refresh_model_availability.run)
        assert started.wait(5), "first refresh did not begin its model request"
        second_result = reliability.refresh_model_availability.run()
        release.set()
        first_result = first_run.result(timeout=10)

    assert second_result == {"available": 0, "unavailable": 0, "skipped": 1}
    assert first_result == {"available": 1, "unavailable": 0, "skipped": 0}
    assert tags_calls == 1
    assert post_calls == 1
    assert _cache_value(real_redis, "ollama", model) == {"available": True}
