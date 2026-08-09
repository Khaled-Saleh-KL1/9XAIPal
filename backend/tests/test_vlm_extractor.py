from app.core.config import settings


def test_extractor_settings_defaults():
    assert settings.extractor_provider == "mineru"           # off by default
    assert settings.extractor_vlm_model.startswith("qwen3-vl")
    assert settings.extractor_vlm_dpi >= 72
    assert settings.extractor_vlm_max_pages == 0             # 0 = unlimited
    assert settings.extractor_vlm_concurrency >= 1
