"""Printed-only startup probe; all SDK calls are mocked."""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types

from app.core.config import Settings
from app.extraction.arabic_capability import (
    ArabicGeminiCapabilityError,
    probe_printed_flash,
)


def test_disabled_probe_uses_no_keys_or_provider_calls():
    calls = []
    result = probe_printed_flash(
        Settings(arabic_ocr_enabled=False, gemini_api_keys_raw="key-one"),
        client_factory=lambda key: calls.append(key),
    )
    assert result == []
    assert calls == []


def test_probe_checks_each_key_on_printed_flash_only():
    seen = []

    class FakeClient:
        def __init__(self, key):
            self.key = key
            self.models = self

        def generate_content(self, *, model, contents, config):
            seen.append((self.key, model, contents, config))
            if self.key == "key-one":
                raise errors.APIError(403, {"error": {"code": 403, "message": "denied"}})
            return SimpleNamespace(text="OK")

        def close(self):
            pass

    result = probe_printed_flash(
        Settings(arabic_ocr_enabled=True, gemini_api_keys_raw="key-one,key-two"),
        client_factory=FakeClient,
    )

    assert result == [
        {"key_index": 0, "success": False, "status": "unavailable"},
        {"key_index": 1, "success": True, "status": "available"},
    ]
    assert len(seen) == 2
    assert all(model == "gemini-3.7-flash" for _, model, _, _ in seen)
    assert all(config.thinking_config.thinking_level == types.ThinkingLevel.LOW for *_, config in seen)
    assert all("gemini-3.1-pro-preview" not in repr(call) for call in seen)


def test_probe_without_working_keys_fails_with_safe_configuration_message():
    class FailingClient:
        models = None

        def __init__(self, _key):
            self.models = self

        def generate_content(self, **_kwargs):
            raise errors.APIError(404, {"error": {"code": 404, "message": "secret-key text"}})

        def close(self):
            pass

    with pytest.raises(ArabicGeminiCapabilityError) as raised:
        probe_printed_flash(
            Settings(arabic_ocr_enabled=True, gemini_api_keys_raw="key-one,key-two"),
            client_factory=FailingClient,
        )

    assert "Gemini" in str(raised.value)
    assert "key-one" not in str(raised.value)
    assert "secret-key" not in str(raised.value)


def _client_raising(error):
    class Client:
        def __init__(self, _key):
            self.models = self

        def generate_content(self, **_kwargs):
            raise error

        def close(self):
            pass

    return Client


@pytest.mark.parametrize(
    "error",
    [
        errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}),
        errors.APIError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}),
        httpx.ReadTimeout("probe timed out"),
        httpx.ConnectError("network down"),
    ],
    ids=["rate_limited", "server_error", "timeout", "network"],
)
def test_transient_probe_failures_do_not_block_startup(error):
    # A rate limit, spent daily quota, provider outage, or network blip at boot
    # says nothing about whether the model is configured correctly. Refusing
    # to start would take English ingestion down with it.
    result = probe_printed_flash(
        Settings(arabic_ocr_enabled=True, gemini_api_keys_raw="key-one,key-two"),
        client_factory=_client_raising(error),
    )

    assert result == [
        {"key_index": 0, "success": False, "status": "unconfirmed"},
        {"key_index": 1, "success": False, "status": "unconfirmed"},
    ]


def test_invalid_api_key_reported_as_http_400_counts_as_unavailable():
    invalid_key = errors.APIError(400, {"error": {
        "code": 400,
        "status": "INVALID_ARGUMENT",
        "details": [{
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": "API_KEY_INVALID",
        }],
    }})

    with pytest.raises(ArabicGeminiCapabilityError):
        probe_printed_flash(
            Settings(arabic_ocr_enabled=True, gemini_api_keys_raw="key-one"),
            client_factory=_client_raising(invalid_key),
        )


def test_one_unavailable_key_and_one_rate_limited_key_still_starts():
    class Client:
        def __init__(self, key):
            self.key = key
            self.models = self

        def generate_content(self, **_kwargs):
            if self.key == "key-one":
                raise errors.APIError(403, {"error": {"code": 403}})
            raise errors.APIError(429, {"error": {"code": 429}})

        def close(self):
            pass

    result = probe_printed_flash(
        Settings(arabic_ocr_enabled=True, gemini_api_keys_raw="key-one,key-two"),
        client_factory=Client,
    )

    assert [outcome["status"] for outcome in result] == ["unavailable", "unconfirmed"]
