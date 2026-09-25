import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_gemini_keys_are_trimmed_and_empty_values_are_dropped():
    cfg = Settings(gemini_api_keys_raw=" k1, ,k2,\nk3 ", _env_file=None)
    assert cfg.gemini_api_keys == ["k1", "k2", "k3"]


def test_gemini_keys_read_the_documented_environment_name(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "first, second")
    cfg = Settings(_env_file=None)
    assert cfg.gemini_api_keys == ["first", "second"]


def test_arabic_features_are_safe_by_default():
    cfg = Settings(_env_file=None)
    assert cfg.arabic_ocr_enabled is False
    assert cfg.arabic_handwritten_ocr_enabled is False
    assert cfg.arabic_gemini_printed_model == "gemini-3.7-flash"
    assert cfg.arabic_gemini_printed_thinking_level == "low"
    assert cfg.arabic_gemini_handwritten_model == "gemini-3.1-pro-preview"


def test_arabic_gemma_uses_a_separate_cloud_endpoint_and_key_list():
    cfg = Settings(
        arabic_gemma_api_keys_raw=" cloud-1, ,cloud-2 ",
        _env_file=None,
    )
    assert cfg.arabic_gemma_base_url == "https://ollama.com"
    assert cfg.arabic_gemma_fallback_model == "gemma4:31b-cloud"
    assert cfg.arabic_gemma_api_keys == ["cloud-1", "cloud-2"]


def test_arabic_gemma_accepts_the_existing_ollama_cloud_key(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "cloud-key")
    cfg = Settings(_env_file=None)
    assert cfg.arabic_gemma_api_keys == ["cloud-key"]


def test_classifier_thresholds_are_bounded():
    with pytest.raises(ValidationError):
        Settings(arabic_classifier_confidence_min=1.1, _env_file=None)
