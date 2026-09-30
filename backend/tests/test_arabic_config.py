import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.extraction.arabic_types import HandwrittenArabicUnavailable


def test_gemini_keys_are_trimmed_and_empty_values_are_dropped():
    cfg = Settings(gemini_api_keys_raw=" k1, ,k2,\nk3 ", _env_file=None)
    assert cfg.gemini_api_keys == ["k1", "k2", "k3"]


def test_gemini_keys_read_the_documented_environment_name(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "first, second")
    cfg = Settings(_env_file=None)
    assert cfg.gemini_api_keys == ["first", "second"]


def test_arabic_features_can_be_disabled_explicitly():
    cfg = Settings(arabic_ocr_enabled=False, _env_file=None)
    assert cfg.arabic_ocr_enabled is False
    assert cfg.arabic_handwritten_ocr_enabled is False
    assert cfg.arabic_classifier_batch_pages == 2
    assert cfg.arabic_router_timeout_seconds == 600.0
    assert cfg.arabic_gemini_printed_model == "gemini-3.7-flash"
    assert cfg.arabic_gemini_printed_thinking_level == "low"
    assert cfg.arabic_gemini_handwritten_model == "gemini-3.1-pro-preview"


def test_arabic_ocr_is_enabled_by_default_and_handwriting_is_disabled():
    cfg = Settings(_env_file=None)
    assert cfg.arabic_ocr_enabled is True
    assert cfg.arabic_handwritten_ocr_enabled is False


def test_compose_and_example_defaults_enable_only_printed_arabic():
    backend = Path(__file__).resolve().parents[1]
    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = (backend / compose_name).read_text(encoding="utf-8")
        arabic_defaults = re.findall(
            r"ARABIC_OCR_ENABLED:\s*\$\{ARABIC_OCR_ENABLED:-([^}]+)\}",
            compose,
        )
        handwritten_defaults = re.findall(
            r"ARABIC_HANDWRITTEN_OCR_ENABLED:\s*"
            r"\$\{ARABIC_HANDWRITTEN_OCR_ENABLED:-([^}]+)\}",
            compose,
        )
        assert arabic_defaults and set(arabic_defaults) == {"true"}
        assert handwritten_defaults and set(handwritten_defaults) == {"false"}

    example = (backend / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^ARABIC_OCR_ENABLED=true$", example, flags=re.MULTILINE)
    assert re.search(
        r"^ARABIC_HANDWRITTEN_OCR_ENABLED=false$", example, flags=re.MULTILINE
    )


def test_handwritten_unavailable_message_matches_public_contract():
    assert HandwrittenArabicUnavailable.public_message == (
        "Handwritten Arabic isn't enabled yet. No text was extracted, and "
        "your original file has been kept."
    )


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
    with pytest.raises(ValidationError):
        Settings(arabic_min_body_letter_share=1.1, _env_file=None)


@pytest.mark.parametrize("invalid", [0, -1])
def test_arabic_router_timeout_must_be_positive(invalid):
    with pytest.raises(ValidationError):
        Settings(arabic_router_timeout_seconds=invalid, _env_file=None)


@pytest.mark.parametrize("invalid", ["medium", "high", "minimal", ""])
def test_printed_flash_rejects_any_thinking_level_except_low(invalid):
    with pytest.raises(ValidationError):
        Settings(arabic_gemini_printed_thinking_level=invalid, _env_file=None)
