"""Cloudflare image response adapters and account/model failover."""

import asyncio
import base64
import gzip
from types import SimpleNamespace

import httpx
import pytest

_ACCOUNT_A = "a" * 32
_ACCOUNT_B = "b" * 32


@pytest.fixture(autouse=True)
def _reset_cloudflare_breakers():
    from app.core import circuit_breaker

    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


def _settings(accounts=f"{_ACCOUNT_A}:secret-a", models="model-one,model-two"):
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


def _mock_request_response(monkeypatch, handler):
    from app.services import cloudflare_images

    def request(url, *, deadline, **kwargs):
        return handler(url, **kwargs)

    monkeypatch.setattr(cloudflare_images, "_request_response", request)


def test_settings_parse_accounts_and_optional_model_order():
    config = _settings(
        f" {_ACCOUNT_A} : token-one ,{_ACCOUNT_B}:token-two ",
        " model-a, ,model-b ",
    )

    assert config.cloudflare_ai_accounts == [
        (_ACCOUNT_A, "token-one"),
        (_ACCOUNT_B, "token-two"),
    ]
    assert config.cloudflare_image_models == ["model-a", "model-b"]


def test_invalid_account_ids_are_ignored_without_logging_their_tokens(caplog):
    valid_token = "valid-account-token"
    invalid_tokens = ("short-token", "uppercase-token", "path-token")
    missing_separator = "account-entry-without-separator"
    config = _settings(
        f"{_ACCOUNT_A}:{valid_token},short-id:{invalid_tokens[0]},"
        f"{'C' * 32}:{invalid_tokens[1]},bad/../path:{invalid_tokens[2]},"
        f"{_ACCOUNT_B}:,{missing_separator}"
    )

    assert config.cloudflare_ai_accounts == [(_ACCOUNT_A, valid_token)]
    assert len(caplog.records) == len(invalid_tokens) + 2
    for token in (valid_token, *invalid_tokens):
        assert token not in caplog.text
    assert missing_separator not in caplog.text


def test_decodes_json_base64_image(monkeypatch):
    from app.services import cloudflare_images

    payload = b"generated-image"
    _mock_request_response(
        monkeypatch,
        lambda *_args, **_kwargs: _response(
            json_body={"result": {"image": base64.b64encode(payload).decode()}}
        ),
    )

    assert cloudflare_images.generate_image("a quiet city", config=_settings()) == payload


def test_accepts_raw_image_bytes(monkeypatch):
    from app.services import cloudflare_images

    payload = b"\x89PNG\r\nimage"
    _mock_request_response(
        monkeypatch,
        lambda *_args, **_kwargs: _response(content=payload, content_type="image/png"),
    )

    assert cloudflare_images.generate_image("a quiet city", config=_settings()) == payload


def test_decodes_gzip_encoded_image_only_once(monkeypatch):
    from app.services import cloudflare_images

    payload = b"generated-image"
    def response_handler(request):
        return httpx.Response(
            200,
            content=gzip.compress(payload),
            headers={"content-type": "image/png", "content-encoding": "gzip"},
            request=request,
        )

    monkeypatch.setattr(
        cloudflare_images,
        "_async_transport",
        lambda: httpx.MockTransport(response_handler),
    )

    assert cloudflare_images.generate_image("prompt", config=_settings()) == payload


def test_streaming_response_above_8_mib_is_aborted(monkeypatch):
    from app.services import cloudflare_images

    chunk_size = 64 * 1024
    chunk_count = (8 * 1024 * 1024) // chunk_size + 4

    class TrackedStream(httpx.AsyncByteStream):
        def __init__(self):
            self.chunks_read = 0
            self.closed = False

        async def __aiter__(self):
            for _ in range(chunk_count):
                self.chunks_read += 1
                yield b"x" * chunk_size

        async def aclose(self):
            self.closed = True

    body = TrackedStream()
    response = httpx.Response(
        200,
        headers={"content-type": "image/png"},
        stream=body,
        request=httpx.Request("POST", "https://api.cloudflare.com/test"),
    )

    async def read_response():
        try:
            return await cloudflare_images._read_bounded_response(response)
        finally:
            await response.aclose()

    result = asyncio.run(read_response())

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

    _mock_request_response(monkeypatch, post)

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
        if _ACCOUNT_A in url:
            return _response(429, json_body={"errors": [{"message": "daily limit"}]})
        return _response(content=b"image", content_type="image/jpeg")

    _mock_request_response(monkeypatch, post)

    result = cloudflare_images.generate_image(
        "prompt", config=_settings(f"{_ACCOUNT_A}:secret-a,{_ACCOUNT_B}:secret-b")
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

    _mock_request_response(monkeypatch, post)

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

    _mock_request_response(monkeypatch, post)

    assert cloudflare_images.generate_image(
        "prompt", config=_settings(f"{_ACCOUNT_A}:secret-a,{_ACCOUNT_B}:secret-b")
    ) == b"image"
    assert [url.rsplit("/", 1)[-1] for url, _ in attempted] == ["model-one", "model-one"]


def test_all_quota_exhausted_accounts_raise_quota_exception(monkeypatch):
    from app.services import cloudflare_images

    _mock_request_response(monkeypatch, lambda *_args, **_kwargs: _response(429))

    with pytest.raises(cloudflare_images.QuotaExhaustedError):
        cloudflare_images.generate_image(
            "prompt", config=_settings(f"{_ACCOUNT_A}:secret-a,{_ACCOUNT_B}:secret-b")
        )


def test_model_failure_before_quota_still_retries_accounts_next_day(monkeypatch):
    from app.services import cloudflare_images

    attempted = {}

    def post(url, **kwargs):
        account = _ACCOUNT_A if _ACCOUNT_A in url else _ACCOUNT_B
        attempted[account] = attempted.get(account, 0) + 1
        if attempted[account] == 1:
            return _response(404)
        return _response(429)

    _mock_request_response(monkeypatch, post)

    with pytest.raises(cloudflare_images.QuotaExhaustedError):
        cloudflare_images.generate_image(
            "prompt",
            config=_settings(
                f"{_ACCOUNT_A}:secret-a,{_ACCOUNT_B}:secret-b", "model-one,model-two"
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

    _mock_request_response(monkeypatch, response)

    result = cloudflare_images.generate_image(
        "prompt",
        config=_settings(f"{_ACCOUNT_A}:secret-a,{_ACCOUNT_B}:secret-b", "model-one"),
    )

    assert result == b"image"
    assert len(attempted) == 1
    assert f"/{_ACCOUNT_B}/" in attempted[0]


def test_all_open_account_breakers_skip_requests_and_raise_non_quota_error(
    monkeypatch,
):
    from app.core import circuit_breaker
    from app.services import cloudflare_images

    for index in range(2):
        for _ in range(circuit_breaker.FAILURE_THRESHOLD):
            circuit_breaker.record_failure(f"cloudflare#{index}")
    attempted = []

    def response(url, **_kwargs):
        attempted.append(url)
        return _response(503)

    _mock_request_response(monkeypatch, response)

    with pytest.raises(RuntimeError) as error:
        cloudflare_images.generate_image(
            "prompt",
            config=_settings(f"{_ACCOUNT_A}:secret-a,{_ACCOUNT_B}:secret-b", "model-one"),
        )

    assert type(error.value).__name__ == "CircuitBreakersOpenError"
    assert not isinstance(error.value, cloudflare_images.QuotaExhaustedError)
    assert attempted == []


def test_model_requests_share_one_total_deadline(monkeypatch):
    from app.services import cloudflare_images

    clock = iter((0.0, 0.0, 0.0, 179.0, 179.0, 180.0))
    monkeypatch.setattr(
        cloudflare_images,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock)),
        raising=False,
    )
    attempted_timeouts = []

    def response(_url, **kwargs):
        attempted_timeouts.append(kwargs["timeout"])
        return _response(503)

    _mock_request_response(monkeypatch, response)

    result = cloudflare_images.generate_image(
        "prompt",
        config=_settings(f"{_ACCOUNT_A}:secret-a", "model-one,model-two,model-three"),
    )

    assert result is None
    assert attempted_timeouts == [60.0, 1.0]


def test_total_deadline_failures_open_the_account_breaker(monkeypatch):
    from app.core import circuit_breaker
    from app.services import cloudflare_images

    now = {"value": 0.0}
    monkeypatch.setattr(
        cloudflare_images,
        "time",
        SimpleNamespace(monotonic=lambda: now["value"]),
        raising=False,
    )

    def timeout_each_request(_url, **kwargs):
        now["value"] += kwargs["timeout"]
        return _response(503)

    _mock_request_response(monkeypatch, timeout_each_request)
    config = _settings(models=",".join(f"model-{i}" for i in range(8)))

    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        assert cloudflare_images.generate_image("prompt", config=config) is None

    assert circuit_breaker.is_open("cloudflare#0")


def test_active_stream_is_cancelled_at_the_total_deadline(monkeypatch):
    from app.services import cloudflare_images

    class HangingStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            self.closed = True

    body = HangingStream()

    def response_handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            stream=body,
            request=request,
        )

    monkeypatch.setattr(
        cloudflare_images,
        "_async_transport",
        lambda: httpx.MockTransport(response_handler),
        raising=False,
    )

    result = cloudflare_images._request_response(
        "https://api.cloudflare.com/test",
        deadline=cloudflare_images.time.monotonic() + 0.02,
        timeout=1.0,
        headers={"Authorization": "Bearer test-only"},
    )

    assert result is None
    assert body.closed


def test_no_configured_accounts_is_a_quiet_noop(monkeypatch, caplog):
    from app.services import cloudflare_images

    def unexpected_request(*args, **kwargs):
        pytest.fail("an unconfigured image client must not make a request")

    monkeypatch.setattr(cloudflare_images, "_request_response", unexpected_request)

    assert cloudflare_images.generate_image("prompt", config=_settings("", "")) is None
    assert not caplog.records


def test_tokens_are_never_written_to_log_records(monkeypatch, caplog):
    from app.services import cloudflare_images

    _mock_request_response(monkeypatch, lambda *_args, **_kwargs: _response(401))

    cloudflare_images.generate_image(
        "prompt", config=_settings(f"{_ACCOUNT_A}:do-not-log-this-token", "model-one")
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
