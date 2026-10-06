import json
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.llm import availability, catalog, resolver
from app.llm.resolver import LLMTarget


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.ttls[key] = ex

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_reason"),
    [
        (402, "needs a paid Ollama plan"),
        (401, "provider authentication failed"),
        (403, "provider authentication failed"),
        (404, "model not found"),
    ],
)
async def test_real_provider_failure_is_cached_as_unavailable(
    monkeypatch, status_code, expected_reason
):
    redis = FakeRedis()
    monkeypatch.setattr(availability, "get_redis", lambda: redis)

    await availability.record_model_result(
        "ollama", "glm-5.3-flash", available=False, status_code=status_code
    )
    result = await availability.get_model_availability(
        [("ollama", "glm-5.3-flash")]
    )

    assert result[("ollama", "glm-5.3-flash")] == {
        "available": False,
        "unavailable_reason": expected_reason,
    }
    key = next(iter(redis.values))
    assert redis.ttls[key] == 6 * 60 * 60
    assert "key" not in key.lower()


@pytest.mark.asyncio
async def test_success_is_cached_as_available(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(availability, "get_redis", lambda: redis)

    await availability.record_model_result("nvidia", "meta/muse-glimmer-30b", available=True)

    result = await availability.get_model_availability(
        [("nvidia", "meta/muse-glimmer-30b")]
    )
    assert result[("nvidia", "meta/muse-glimmer-30b")] == {
        "available": True,
        "unavailable_reason": None,
    }
    assert json.loads(next(iter(redis.values.values()))) == {"available": True}


@pytest.mark.asyncio
async def test_cache_miss_and_redis_error_degrade_to_available(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(availability, "get_redis", lambda: redis)
    models = [("ollama", "unknown-model")]
    assert (await availability.get_model_availability(models))[models[0]] == {
        "available": True,
        "unavailable_reason": None,
    }

    def unavailable_redis():
        raise ConnectionError("Redis is down")

    monkeypatch.setattr(availability, "get_redis", unavailable_redis)
    await availability.record_model_result(
        "ollama", "unknown-model", available=False, status_code=402
    )
    assert (await availability.get_model_availability(models))[models[0]]["available"] is True


class FakeTagsResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "models": [
                {"name": "gemma4:31b", "size": 100},
                {"name": "glm-5.3-flash", "size": 200},
            ]
        }


class FakeTagsClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return FakeTagsResponse()


@pytest.mark.asyncio
async def test_remote_catalog_models_are_cloud_and_report_cached_availability(monkeypatch):
    monkeypatch.setattr(settings, "ollama_base_url", "https://ollama.com")
    monkeypatch.setattr(settings, "nvidia_api_key", "")
    monkeypatch.setattr(resolver, "ollama_reachable", AsyncMock(return_value=True))
    target = LLMTarget("ollama", "", settings.ollama_base_url, "gemma4:31b", "m", "m")
    monkeypatch.setattr(resolver, "resolve_llm", AsyncMock(return_value=target))
    monkeypatch.setattr(catalog.httpx, "AsyncClient", FakeTagsClient)

    async def cached(models):
        return {
            ("ollama", name): {
                "available": name != "glm-5.3-flash",
                "unavailable_reason": (
                    "needs a paid Ollama plan" if name == "glm-5.3-flash" else None
                ),
            }
            for _, name in models
        }

    monkeypatch.setattr(availability, "get_model_availability", cached)

    result = await catalog.list_chat_models()
    rows = {model["name"]: model for model in result["models"]}

    assert rows["gemma4:31b"]["is_cloud"] is True
    assert rows["gemma4:31b"]["available"] is True
    assert rows["glm-5.3-flash"]["is_cloud"] is True
    assert rows["glm-5.3-flash"]["available"] is False
    assert rows["glm-5.3-flash"]["unavailable_reason"] == "needs a paid Ollama plan"


def test_local_ollama_tags_are_not_marked_cloud(monkeypatch):
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama:11434")

    assert catalog._is_cloud("gemma4:26b", 200) is False
    assert catalog._is_cloud("gemma4:26b-cloud", 200) is True
