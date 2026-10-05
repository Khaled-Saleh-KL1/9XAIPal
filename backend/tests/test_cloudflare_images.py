"""Cloudflare image response adapters and account/model failover."""

import base64
from contextlib import contextmanager

import httpx
import pytest


@pytest.fixture(autouse=True)
def _reset_cloudflare_breakers():
    from app.core import circuit_breaker

    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


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


def _mock_stream(monkeypatch, handler):
    @contextmanager
    def stream(_method, url, **kwargs):
        response = handler(url, **kwargs)
        try:
            yield response
        finally:
            response.close()

    monkeypatch.setattr(httpx, "stream", stream)


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
    _mock_stream(
        monkeypatch,
        lambda *_args, **_kwargs: _response(
            json_body={"result": {"image": base64.b64encode(payload).decode()}}
        ),
    )

    assert cloudflare_images.generate_image("a quiet city", config=_settings()) == payload


def test_accepts_raw_image_bytes(monkeypatch):
    from app.services import cloudflare_images

    payload = b"\x89PNG\r\nimage"
    _mock_stream(
        monkeypatch,
        lambda *_args, **_kwargs: _response(content=payload, content_type="image/png"),
    )

    assert cloudflare_images.generate_image("a quiet city", config=_settings()) == payload


def test_streaming_response_above_8_mib_is_aborted(monkeypatch):
    from app.services import cloudflare_images

    chunk_size = 64 * 1024
    chunk_count = (8 * 1024 * 1024) // chunk_size + 4

    class TrackedStream(httpx.SyncByteStream):
        def __init__(self):
            self.chunks_read = 0
            self.closed = False

        def __iter__(self):
            for _ in range(chunk_count):
                self.chunks_read += 1
                yield b"x" * chunk_size

        def close(self):
            self.closed = True

    body = TrackedStream()
    response = httpx.Response(
        200,
        headers={"content-type": "image/png"},
        stream=body,
        request=httpx.Request("POST", "https://api.cloudflare.com/test"),
    )

    @contextmanager
    def stream(*_args, **_kwargs):
        try:
            yield response
        finally:
            response.close()

    monkeypatch.setattr(cloudflare_images.httpx, "stream", stream)

    result = cloudflare_images.generate_image("prompt", config=_settings())

    assert result is None
    assert body.closed
    assert body.chunks_read < chunk_count


def test_oversized_json_image_is_rejected_before_base64_decode(monkeypatch):
    from app.services import cloudflare_images

    encoded = "A" * (8 * 1024 * 1024 + 1)
    response = _response(json_body={"result": {"image": encoded}})

    def unexpected_decode(*_args, **_kwargs):
        pytest.fail("oversized JSON image reached base64 decoding")

    monkeypatch.setattr(cloudflare_images.base64, "b64decode", unexpected_decode)

    assert cloudflare_images._image_bytes(response) is None


def test_flux_2_request_uses_multipart_form_data(monkeypatch):
    from app.services import cloudflare_images

    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _response(content=b"image", content_type="image/png")

    _mock_stream(monkeypatch, post)

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

    _mock_stream(monkeypatch, post)

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

    _mock_stream(monkeypatch, post)

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

    _mock_stream(monkeypatch, post)

    assert cloudflare_images.generate_image(
        "prompt", config=_settings("account-a:secret-a,account-b:secret-b")
    ) == b"image"
    assert [url.rsplit("/", 1)[-1] for url, _ in attempted] == ["model-one", "model-one"]


def test_all_quota_exhausted_accounts_raise_quota_exception(monkeypatch):
    from app.services import cloudflare_images

    _mock_stream(monkeypatch, lambda *_args, **_kwargs: _response(429))

    with pytest.raises(cloudflare_images.QuotaExhaustedError):
        cloudflare_images.generate_image(
            "prompt", config=_settings("account-a:secret-a,account-b:secret-b")
        )


def test_model_failure_before_quota_still_retries_accounts_next_day(monkeypatch):
    from app.services import cloudflare_images

    attempted = {}

    def post(url, **kwargs):
        account = "account-a" if "account-a" in url else "account-b"
        attempted[account] = attempted.get(account, 0) + 1
        if attempted[account] == 1:
            return _response(404)
        return _response(429)

    _mock_stream(monkeypatch, post)

    with pytest.raises(cloudflare_images.QuotaExhaustedError):
        cloudflare_images.generate_image(
            "prompt",
            config=_settings(
                "account-a:secret-a,account-b:secret-b", "model-one,model-two"
            ),
        )


def test_open_account_is_skipped_when_another_account_is_healthy(monkeypatch):
    from app.core import circuit_breaker
    from app.services import cloudflare_images

    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        circuit_breaker.record_failure("cloudflare#0")
    attempted = []

    def response(url, **_kwargs):
        attempted.append(url)
        return _response(content=b"image", content_type="image/png")

    _mock_stream(monkeypatch, response)

    result = cloudflare_images.generate_image(
        "prompt",
        config=_settings("account-a:secret-a,account-b:secret-b", "model-one"),
    )

    assert result == b"image"
    assert len(attempted) == 1
    assert "/account-b/" in attempted[0]


def test_all_open_account_breakers_skip_requests_and_raise_for_later_retry(monkeypatch):
    from app.core import circuit_breaker
    from app.services import cloudflare_images

    for index in range(2):
        for _ in range(circuit_breaker.FAILURE_THRESHOLD):
            circuit_breaker.record_failure(f"cloudflare#{index}")
    attempted = []

    def response(url, **_kwargs):
        attempted.append(url)
        return _response(503)

    _mock_stream(monkeypatch, response)

    with pytest.raises(cloudflare_images.QuotaExhaustedError):
        cloudflare_images.generate_image(
            "prompt",
            config=_settings("account-a:secret-a,account-b:secret-b", "model-one"),
        )

    assert attempted == []


def test_no_configured_accounts_is_a_quiet_noop(monkeypatch, caplog):
    from app.services import cloudflare_images

    def unexpected_request(*args, **kwargs):
        pytest.fail("an unconfigured image client must not make a request")

    monkeypatch.setattr(cloudflare_images.httpx, "stream", unexpected_request)

    assert cloudflare_images.generate_image("prompt", config=_settings("", "")) is None
    assert not caplog.records


def test_tokens_are_never_written_to_log_records(monkeypatch, caplog):
    from app.services import cloudflare_images

    _mock_stream(monkeypatch, lambda *_args, **_kwargs: _response(401))

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
