"""Unit tests for Ollama Cloud Bearer auth (app.llm.ollama_client._ollama_headers).

Verifies the header helper adds `Authorization: Bearer <key>` when
`settings.ollama_api_key` is set, and stays empty (keyless) for local Ollama
where no key is configured.
"""

from app.core.config import settings
from app.llm.ollama_client import _ollama_headers


def test_ollama_headers_include_bearer_when_key_set(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "sk-test")
    assert _ollama_headers() == {"Authorization": "Bearer sk-test"}


def test_ollama_headers_empty_when_key_blank(monkeypatch):
    monkeypatch.setattr(settings, "ollama_api_key", "")
    assert _ollama_headers() == {}
