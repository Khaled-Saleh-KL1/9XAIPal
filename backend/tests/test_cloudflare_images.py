"""Cloudflare image response adapters and account/model failover."""

import base64

import httpx
import pytest


def _settings(accounts="account-a:secret-a", models="model-one,model-two"):
    from app.core.config import Settings

    return Settings(
        _env_file=None,
        CLOUDFLARE_AI_ACCOUNTS=accounts,
        CLOUDFLARE_IMAGE_MODELS=models,
    )


def _response(status=200, *, json_body=None, content=b"", content_type="application/json"):
    request = httpx.Request("POST", "https://api.cloudflare.com/test")
    if json_body is not None:
        return httpx.Response(
            status,
            json=json_body,
            headers={"content-type": content_type},
            request=request,
        )
    return httpx.Response(
        status,
        content=content,
        headers={"content-type": content_type},
        request=request,
    )


def test_settings_parse_accounts_and_optional_model_order():
    config = _settings(" first : token-one ,second:token-two ", " model-a, ,model-b ")

    assert config.cloudflare_ai_accounts == [
        ("first", "token-one"),
        ("second", "token-two"),
    ]
    assert config.cloudflare_image_models == ["model-a", "model-b"]


def test_decodes_json_base64_image(monkeypatch):
    from app.services import cloudflare_images

    payload = b"generated-image"
    monkeypatch.setattr(
        cloudflare_images.httpx,
        "post",
        lambda *args, **kwargs: _response(
            json_body={"result": {"image": base64.b64encode(payload).decode()}}
        ),
    )

    assert cloudflare_images.generate_image("a quiet city", config=_settings()) == payload


def test_accepts_raw_image_bytes(monkeypatch):
    from app.services import cloudflare_images

    payload = b"\x89PNG\r\nimage"
    monkeypatch.setattr(
        cloudflare_images.httpx,
        "post",
        lambda *args, **kwargs: _response(content=payload, content_type="image/png"),
    )

    assert cloudflare_images.generate_image("a quiet city", config=_settings()) == payload


def test_flux_2_request_uses_multipart_form_data(monkeypatch):
    from app.services import cloudflare_images

    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _response(content=b"image", content_type="image/png")

    monkeypatch.setattr(cloudflare_images.httpx, "post", post)

    result = cloudflare_images.generate_image(
        "a quiet city", config=_settings(models="@cf/black-forest-labs/flux-2-klein-4b")
    )

    assert result == b"image"
    assert "files" in captured
    assert captured["files"]["prompt"][1] == "a quiet city"
    assert captured["files"]["width"][1] == "1024"
    assert captured["files"]["height"][1] == "768"
    assert "json" not in captured


def test_quota_on_one_account_restarts_at_first_model_on_next(monkeypatch):
    from app.services import cloudflare_images

    attempted = []

    def post(url, **kwargs):
        attempted.append((url, kwargs["headers"]["Authorization"]))
        if "account-a" in url:
            return _response(429, json_body={"errors": [{"message": "daily limit"}]})
        return _response(content=b"image", content_type="image/jpeg")

    monkeypatch.setattr(cloudflare_images.httpx, "post", post)

    result = cloudflare_images.generate_image(
        "prompt", config=_settings("account-a:secret-a,account-b:secret-b")
    )

    assert result == b"image"
    assert [url.rsplit("/", 1)[-1] for url, _ in attempted] == [
        "model-one",
        "model-one",
    ]
    assert [authorization for _, authorization in attempted] == [
        "Bearer secret-a",
        "Bearer secret-b",
    ]


def test_model_404_tries_next_model_on_same_account(monkeypatch):
    from app.services import cloudflare_images

    attempted = []

    def post(url, **kwargs):
        attempted.append(url.rsplit("/", 1)[-1])
        return _response(404) if len(attempted) == 1 else _response(content=b"image", content_type="image/jpeg")

    monkeypatch.setattr(cloudflare_images.httpx, "post", post)

    assert cloudflare_images.generate_image("prompt", config=_settings()) == b"image"
    assert attempted == ["model-one", "model-two"]


def test_auth_failure_skips_remaining_models_for_that_account(monkeypatch):
    from app.services import cloudflare_images

    attempted = []

    def post(url, **kwargs):
        attempted.append((url, kwargs["headers"]["Authorization"]))
        if kwargs["headers"]["Authorization"] == "Bearer secret-a":
            return _response(401)
        return _response(content=b"image", content_type="image/jpeg")

    monkeypatch.setattr(cloudflare_images.httpx, "post", post)

    assert cloudflare_images.generate_image(
        "prompt", config=_settings("account-a:secret-a,account-b:secret-b")
    ) == b"image"
    assert [url.rsplit("/", 1)[-1] for url, _ in attempted] == ["model-one", "model-one"]


def test_all_quota_exhausted_accounts_raise_quota_exception(monkeypatch):
    from app.services import cloudflare_images

    monkeypatch.setattr(
        cloudflare_images.httpx,
        "post",
        lambda *args, **kwargs: _response(429),
    )

    with pytest.raises(cloudflare_images.QuotaExhaustedError):
        cloudflare_images.generate_image(
            "prompt", config=_settings("account-a:secret-a,account-b:secret-b")
        )


def test_no_configured_accounts_is_a_quiet_noop(monkeypatch, caplog):
    from app.services import cloudflare_images

    def unexpected_request(*args, **kwargs):
        pytest.fail("an unconfigured image client must not make a request")

    monkeypatch.setattr(cloudflare_images.httpx, "post", unexpected_request)

    assert cloudflare_images.generate_image("prompt", config=_settings("", "")) is None
    assert not caplog.records


def test_tokens_are_never_written_to_log_records(monkeypatch, caplog):
    from app.services import cloudflare_images

    monkeypatch.setattr(
        cloudflare_images.httpx,
        "post",
        lambda *args, **kwargs: _response(401),
    )

    cloudflare_images.generate_image(
        "prompt", config=_settings("account-a:do-not-log-this-token", "model-one")
    )

    assert "do-not-log-this-token" not in caplog.text


def test_default_model_order_excludes_inpainting():
    from app.services.cloudflare_images import DEFAULT_MODELS

    assert DEFAULT_MODELS == [
        "@cf/black-forest-labs/flux-1-schnell",
        "@cf/black-forest-labs/flux-2-klein-4b",
        "@cf/bytedance/stable-diffusion-xl-lightning",
        "@cf/stabilityai/stable-diffusion-xl-base-1.0",
        "@cf/leonardo/phoenix-1.0",
        "@cf/leonardo/lucid-origin",
        "@cf/black-forest-labs/flux-2-dev",
        "@cf/black-forest-labs/flux-2-klein-9b",
    ]
