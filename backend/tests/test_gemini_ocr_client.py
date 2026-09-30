from collections import deque
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types

from app.extraction.arabic_types import (
    GeminiKeysExhausted,
    GeminiOutputInvalid,
    GeminiRequestInvalid,
    RenderedPage,
)
from app.extraction.gemini_ocr_client import GeminiOcrClient


PAGE_1 = "<!-- PAGE:1 -->\nمرحبا\n<!-- END_PAGE:1 -->"
PAGE_2 = "<!-- PAGE:2 -->\nالعالم\n<!-- END_PAGE:2 -->"


def page(number: int) -> RenderedPage:
    return RenderedPage(page_number=number, png=b"png-bytes")


def complete_validator(text: str, page_numbers: tuple[int, ...]) -> int:
    complete = 0
    for number in page_numbers:
        if f"<!-- PAGE:{number} -->" not in text or f"<!-- END_PAGE:{number} -->" not in text:
            break
        complete += 1
    return complete


def api_error(
    code: int,
    *,
    reason=None,
    quota_id=None,
    retry_after=None,
    message="mock failure",
) -> errors.APIError:
    details = []
    if reason:
        details.append({"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason})
    if quota_id:
        details.append({
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{
                "quotaId": quota_id,
                "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
            }],
        })
    response = httpx.Response(
        status_code=code,
        headers={"Retry-After": str(retry_after)} if retry_after is not None else {},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
    )
    return errors.APIError(
        code=code,
        response_json={"error": {"code": code, "message": message, "details": details}},
        response=response,
    )


def response(text: str | None, *, thoughts: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=11,
            candidates_token_count=17,
            thoughts_token_count=thoughts,
            total_token_count=28 + thoughts,
        ),
    )


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.calls = 0
        self.last_config = None
        self.last_contents = None
        self.requests = []

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        self.last_model = model
        self.last_contents = contents
        self.last_config = config
        self.requests.append({"model": model, "contents": contents, "config": config})
        if not self.outcomes:
            raise AssertionError("unexpected extra Gemini call")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def fake_clients():
    return {
        "secret-key-1": FakeClient([response(PAGE_1)]),
        "secret-key-2": FakeClient([response(PAGE_1)]),
    }


def make_client(
    keys: str,
    fake_clients,
    *,
    gateway_key="",
    writing_style="printed",
    sleep=lambda _seconds: None,
    jitter=lambda low, _high: low,
    **settings_overrides,
):
    from app.core.config import Settings

    settings = Settings(
        gemini_api_keys_raw=keys,
        modelgateway_api_key=gateway_key,
        arabic_gemini_printed_model="gemini-test-flash",
        arabic_ocr_max_output_tokens=1234,
        **settings_overrides,
    )
    options = {
        "settings": settings,
        "client_factory": lambda api_key: fake_clients[api_key],
        "sleep": sleep,
        "jitter": jitter,
    }
    if writing_style != "printed":
        options["writing_style"] = writing_style
    return GeminiOcrClient(**options)


def test_429_rotates_to_next_key_and_never_logs_key_material(fake_clients, caplog):
    fake_clients["secret-key-1"] = FakeClient([api_error(429)])
    fake_clients["secret-key-2"] = FakeClient([response(PAGE_1)])

    result = make_client("secret-key-1,secret-key-2", fake_clients).generate_batch(
        [page(1)], validator=complete_validator
    )

    assert result.key_index == 1
    assert fake_clients["secret-key-1"].models.calls == 1
    assert fake_clients["secret-key-2"].models.calls == 1
    assert result.attempt_count == 2
    assert "secret-key-1" not in caplog.text
    assert "secret-key-2" not in caplog.text


def test_bad_request_does_not_rotate(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([api_error(400)])

    with pytest.raises(GeminiRequestInvalid):
        make_client("secret-key-1,secret-key-2", fake_clients).generate_batch(
            [page(1)], validator=complete_validator
        )

    assert fake_clients["secret-key-2"].models.calls == 0


def test_invalid_api_key_as_http_400_rotates_and_is_skipped_later(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([api_error(400, reason="API_KEY_INVALID")])
    fake_clients["secret-key-2"] = FakeClient([response(PAGE_1), response(PAGE_1)])
    client = make_client("secret-key-1,secret-key-2", fake_clients)

    first = client.generate_batch([page(1)], validator=complete_validator)
    second = client.generate_batch([page(1)], validator=complete_validator)

    assert (first.key_index, second.key_index) == (1, 1)
    assert fake_clients["secret-key-1"].models.calls == 1
    assert first.attempt_metadata[0]["failure_kind"] == "authentication"


def test_short_retry_after_retries_same_key_once(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([
        api_error(429, quota_id="GenerateRequestsPerMinutePerProjectPerModel", retry_after=0.2),
        response(PAGE_1),
    ])
    sleeps = []

    result = make_client(
        "secret-key-1,secret-key-2", fake_clients, sleep=sleeps.append
    ).generate_batch([page(1)], validator=complete_validator)

    assert result.key_index == 0
    assert sleeps == [0.2]
    assert fake_clients["secret-key-1"].models.calls == 2
    assert result.attempt_metadata[0]["quota_scope"] == "minute"


def test_long_retry_after_rotates_without_sleep(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([api_error(429, retry_after=20)])
    sleeps = []

    result = make_client(
        "secret-key-1,secret-key-2", fake_clients, sleep=sleeps.append,
        arabic_gemini_retry_after_max_seconds=10,
    ).generate_batch([page(1)], validator=complete_validator)

    assert result.key_index == 1
    assert sleeps == []


def test_daily_quota_key_is_skipped_across_batches_but_minute_limit_is_not(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([
        api_error(429, quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
    ])
    fake_clients["secret-key-2"] = FakeClient([response(PAGE_1), response(PAGE_1)])
    client = make_client("secret-key-1,secret-key-2", fake_clients)

    first = client.generate_batch([page(1)], validator=complete_validator)
    second = client.generate_batch([page(1)], validator=complete_validator)

    assert fake_clients["secret-key-1"].models.calls == 1
    assert (first.key_index, second.key_index) == (1, 1)
    assert first.attempt_metadata[0]["failure_kind"] == "daily_quota"
    assert first.attempt_metadata[0]["quota_id"].startswith("GenerateRequestsPerDay")


def test_per_minute_limit_can_use_key_again_on_later_batch(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([
        api_error(429, quota_id="GenerateRequestsPerMinutePerProjectPerModel"),
        response(PAGE_1),
    ])
    fake_clients["secret-key-2"] = FakeClient([response(PAGE_1)])
    client = make_client("secret-key-1,secret-key-2", fake_clients)

    first = client.generate_batch([page(1)], validator=complete_validator)
    second = client.generate_batch([page(1)], validator=complete_validator)

    assert (first.key_index, second.key_index) == (1, 0)
    assert fake_clients["secret-key-1"].models.calls == 2


def test_request_uses_low_thinking_high_resolution_and_page_image_parts(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([response(f"{PAGE_1}\n{PAGE_2}")])
    make_client("secret-key-1", fake_clients).generate_batch(
        [page(1), page(2)], validator=complete_validator
    )
    models = fake_clients["secret-key-1"].models

    assert models.last_model == "gemini-test-flash"
    assert models.last_config.max_output_tokens == 1234
    assert models.last_config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert models.last_config.media_resolution is None
    prompt = models.last_contents[0].text
    assert [part.text for part in models.last_contents[1:] if part.text] == ["PAGE 1", "PAGE 2"]
    for instruction in (
        "rightmost column", "top to bottom", "leftmost", "Do not translate",
        "summarize", "spelling correction", "diacritics", "historical spelling",
        "headings", "lists", "tables", "displayed equations", "captions", "[غير واضح]",
    ):
        assert instruction in prompt
    image_parts = [part for part in models.last_contents if part.inline_data]
    assert len(image_parts) == 2
    assert all(part.inline_data.mime_type == "image/png" for part in image_parts)
    assert all(
        part.media_resolution.level == types.PartMediaResolutionLevel.MEDIA_RESOLUTION_HIGH
        for part in image_parts
    )


def test_default_client_factory_configures_request_timeout(monkeypatch):
    from app.core.config import Settings
    from app.extraction import gemini_ocr_client

    captured = {}

    def fake_sdk_client(*, api_key, http_options):
        captured["http_options"] = http_options
        return FakeClient([response(PAGE_1)])

    monkeypatch.setattr(gemini_ocr_client.genai, "Client", fake_sdk_client)
    client = GeminiOcrClient(
        settings=Settings(gemini_api_keys_raw="test-key", arabic_gemini_timeout_seconds=2.5),
    )
    client.generate_batch([page(1)], validator=complete_validator)

    assert captured["http_options"].timeout == 2500
    assert captured["http_options"].base_url is None


def test_gateway_client_factory_uses_configured_base_url(monkeypatch):
    from app.core.config import Settings
    from app.extraction import gemini_ocr_client

    captured = {}

    def fake_sdk_client(*, api_key, http_options):
        captured["api_key"] = api_key
        captured["http_options"] = http_options
        return FakeClient([response(PAGE_1)])

    monkeypatch.setattr(gemini_ocr_client.genai, "Client", fake_sdk_client)
    client = GeminiOcrClient(
        settings=Settings(
            modelgateway_api_key="gateway-secret",
            modelgateway_base_url="https://gateway.example",
        ),
        sleep=lambda _seconds: None,
    )

    result = client.generate_batch([page(1)], validator=complete_validator)

    assert captured["api_key"] == "gateway-secret"
    assert captured["http_options"].base_url == "https://gateway.example"
    assert result.provider == "modelgateway_gemini"
    assert result.model == "gemini-3.8-flash"


def test_gateway_sends_one_page_without_optional_generation_options():
    gateway = FakeClient([response(PAGE_1), response(PAGE_2)])
    client = make_client(
        "",
        {"gateway-secret": gateway},
        gateway_key="gateway-secret",
    )

    result = client.generate_batch([page(1), page(2)], validator=complete_validator)

    assert result.provider == "modelgateway_gemini"
    assert result.model == "gemini-3.8-flash"
    assert [request["model"] for request in gateway.models.requests] == [
        "gemini-3.8-flash",
        "gemini-3.8-flash",
    ]
    assert [
        len([part for part in request["contents"] if part.inline_data])
        for request in gateway.models.requests
    ] == [1, 1]
    assert all(request["config"].thinking_config is None for request in gateway.models.requests)
    assert all(request["config"].max_output_tokens == 1234 for request in gateway.models.requests)
    assert all(
        part.media_resolution is None
        for request in gateway.models.requests
        for part in request["contents"]
        if part.inline_data
    )


@pytest.mark.parametrize("code", [400, 403, 502, 503, 504])
def test_gateway_transient_upstream_errors_retry_without_disabling_key(code):
    gateway = FakeClient([
        api_error(code, message="Upstream provider access denied; try another route"),
        response(PAGE_1),
        response(PAGE_1),
    ])
    sleeps = []
    client = make_client(
        "",
        {"gateway-secret": gateway},
        gateway_key="gateway-secret",
        sleep=sleeps.append,
        arabic_gemini_gateway_max_wait_seconds=1,
    )

    first = client.generate_batch([page(1)], validator=complete_validator)
    second = client.generate_batch([page(1)], validator=complete_validator)

    assert first.provider == second.provider == "modelgateway_gemini"
    assert first.attempt_metadata[0]["failure_kind"] == "transient_http"
    assert first.attempt_metadata[0]["provider"] == "modelgateway_gemini"
    assert sleeps == [0.25]
    assert gateway.models.calls == 3


def test_gateway_retries_use_exponential_jitter():
    gateway = FakeClient([
        api_error(503, message="Upstream provider unavailable"),
        api_error(503, message="Upstream provider unavailable"),
        response(PAGE_1),
    ])
    sleeps = []
    client = make_client(
        "",
        {"gateway-secret": gateway},
        gateway_key="gateway-secret",
        sleep=sleeps.append,
        jitter=lambda _low, high: high,
        arabic_gemini_gateway_max_wait_seconds=2,
    )

    client.generate_batch([page(1)], validator=complete_validator)

    assert sleeps == [0.375, 0.75]


def test_google_generation_options_can_be_disabled(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([response(PAGE_1)])

    make_client(
        "secret-key-1",
        fake_clients,
        arabic_gemini_send_thinking_config=False,
        arabic_gemini_send_media_resolution=False,
    ).generate_batch([page(1)], validator=complete_validator)

    request = fake_clients["secret-key-1"].models.requests[0]
    assert request["config"].thinking_config is None
    image_part = next(part for part in request["contents"] if part.inline_data)
    assert image_part.media_resolution is None


def test_gateway_plain_403_is_authentication_and_skips_gateway_on_later_batches():
    gateway = FakeClient([api_error(403, message="API key not valid")])
    google = FakeClient([response(PAGE_1), response(PAGE_1)])
    client = make_client(
        "google-secret",
        {"gateway-secret": gateway, "google-secret": google},
        gateway_key="gateway-secret",
    )

    first = client.generate_batch([page(1)], validator=complete_validator)
    second = client.generate_batch([page(1)], validator=complete_validator)

    assert first.provider == second.provider == "gemini_arabic_flash"
    assert first.attempt_metadata[0]["failure_kind"] == "authentication"
    assert gateway.models.calls == 1
    assert google.models.calls == 2


def test_gateway_honors_retry_after_beyond_google_retry_limit():
    gateway = FakeClient([api_error(429, retry_after=30), response(PAGE_1)])
    sleeps = []
    client = make_client(
        "",
        {"gateway-secret": gateway},
        gateway_key="gateway-secret",
        sleep=sleeps.append,
    )

    result = client.generate_batch([page(1)], validator=complete_validator)

    assert result.provider == "modelgateway_gemini"
    assert sleeps == [30.0]
    assert gateway.models.calls == 2


def test_gateway_retry_after_is_bounded_by_provider_wait_budget(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([response(PAGE_1)])
    gateway = FakeClient([api_error(503, retry_after=2.1)])
    sleeps = []
    client = make_client(
        "secret-key-1",
        {**fake_clients, "gateway-secret": gateway},
        gateway_key="gateway-secret",
        sleep=sleeps.append,
        arabic_gemini_gateway_max_wait_seconds=2,
    )

    result = client.generate_batch([page(1)], validator=complete_validator)

    assert result.provider == "gemini_arabic_flash"
    assert gateway.models.calls == 1
    assert sleeps == []


def test_gateway_exponential_backoff_stops_at_total_wait_budget(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([response(PAGE_1)])
    gateway = FakeClient([
        api_error(503, message="Upstream provider unavailable"),
        api_error(503, message="Upstream provider unavailable"),
        response(PAGE_1),
    ])
    sleeps = []
    client = make_client(
        "secret-key-1",
        {**fake_clients, "gateway-secret": gateway},
        gateway_key="gateway-secret",
        sleep=sleeps.append,
        jitter=lambda _low, high: high,
        arabic_gemini_gateway_max_wait_seconds=0.5,
    )

    result = client.generate_batch([page(1)], validator=complete_validator)

    assert result.provider == "gemini_arabic_flash"
    assert gateway.models.calls == 2
    assert sleeps == [0.375]


def test_handwritten_gateway_and_google_use_their_respective_pro_models():
    gateway = FakeClient([response(PAGE_1)])
    result = make_client(
        "google-secret",
        {"gateway-secret": gateway, "google-secret": FakeClient([response(PAGE_1)])},
        gateway_key="gateway-secret",
        writing_style="handwritten",
    ).generate_batch([page(1)], validator=complete_validator)

    assert result.provider == "modelgateway_gemini"
    assert gateway.models.last_model == "gemini-3.1-pro"
    assert result.model == "gemini-3.1-pro"


def test_timeout_uses_timeout_failure_kind_and_bounded_exponential_retry(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([
        httpx.ReadTimeout("simulated"),
        httpx.ReadTimeout("simulated"),
        httpx.ReadTimeout("simulated"),
    ])
    sleeps = []
    client = make_client("secret-key-1", fake_clients, sleep=sleeps.append)

    with pytest.raises(GeminiKeysExhausted) as caught:
        client.generate_batch([page(1)], validator=complete_validator)

    assert caught.value.final_kind == "timeout"
    assert fake_clients["secret-key-1"].models.calls == 3
    assert len(sleeps) == 2
    assert sleeps[1] >= 2 * sleeps[0]
    assert caught.value.attempt_metadata[0]["failure_kind"] == "timeout"


@pytest.mark.parametrize("code", [401, 403])
def test_auth_errors_rotate_to_next_key(fake_clients, code):
    fake_clients["secret-key-1"] = FakeClient([api_error(code)])

    result = make_client("secret-key-1,secret-key-2", fake_clients).generate_batch(
        [page(1)], validator=complete_validator
    )

    assert result.key_index == 1
    assert fake_clients["secret-key-2"].models.calls == 1


@pytest.mark.parametrize(
    "first_outcome",
    [
        pytest.param(api_error(408), id="http-408"),
        pytest.param(api_error(503), id="http-503"),
        pytest.param(httpx.ConnectError("mock network failure"), id="network"),
    ],
)
def test_transient_failures_retry_once_then_rotate(fake_clients, first_outcome):
    fake_clients["secret-key-1"] = FakeClient(
        [first_outcome, api_error(429)]
    )

    result = make_client("secret-key-1,secret-key-2", fake_clients).generate_batch(
        [page(1)], validator=complete_validator
    )

    assert result.key_index == 1
    assert fake_clients["secret-key-1"].models.calls == 2
    assert fake_clients["secret-key-2"].models.calls == 1


def test_invalid_or_truncated_output_gets_one_same_key_retry(fake_clients):
    fake_clients["secret-key-1"] = FakeClient(
        [response("<!-- PAGE:1 -->\nincomplete"), response(PAGE_1)]
    )

    result = make_client("secret-key-1,secret-key-2", fake_clients).generate_batch(
        [page(1)], validator=complete_validator
    )

    assert result.key_index == 0
    assert result.text == PAGE_1
    assert fake_clients["secret-key-1"].models.calls == 2
    assert fake_clients["secret-key-2"].models.calls == 0


def test_usage_includes_billed_thought_tokens_and_retries(fake_clients):
    fake_clients["secret-key-1"] = FakeClient(
        [response("bad", thoughts=3), response(PAGE_1, thoughts=5)]
    )

    result = make_client("secret-key-1", fake_clients).generate_batch(
        [page(1)], validator=complete_validator
    )

    assert result.usage.prompt_tokens == 22
    assert result.usage.output_tokens == 34
    assert result.usage.thought_tokens == 8
    assert result.usage.total_tokens == 64
    assert result.attempt_count == 2


def test_no_configured_keys_fails_without_calling_provider(fake_clients):
    with pytest.raises(GeminiKeysExhausted) as caught:
        make_client(" , ", fake_clients).generate_batch(
            [page(1)], validator=complete_validator
        )

    assert caught.value.final_kind == "no_keys_configured"
    assert caught.value.attempt_usage == ()


def test_exhaustion_retains_longest_valid_partial_without_key_material(fake_clients):
    fake_clients["secret-key-1"] = FakeClient(
        [response(PAGE_1), response("bad")]
    )
    fake_clients["secret-key-2"] = FakeClient(
        [api_error(429)]
    )

    with pytest.raises(GeminiKeysExhausted) as caught:
        make_client("secret-key-1,secret-key-2", fake_clients).generate_batch(
            [page(1), page(2)], validator=complete_validator
        )

    error = caught.value
    assert error.final_kind == "rate_limited"
    assert error.best_partial is not None
    assert error.best_partial.text == PAGE_1
    assert error.best_partial.key_index == 0
    assert len(error.attempt_usage) == 2
    assert "secret-key-1" not in str(error)
    assert "secret-key-2" not in str(error)


def test_all_invalid_outputs_raise_categorized_exhaustion(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([response(None), response("still bad")])

    with pytest.raises(GeminiKeysExhausted) as caught:
        make_client("secret-key-1", fake_clients).generate_batch(
            [page(1)], validator=complete_validator
        )

    assert caught.value.final_kind == "invalid_output"
    assert caught.value.best_partial is None
    assert fake_clients["secret-key-1"].models.calls == 2


def test_output_failure_type_is_publicly_available():
    assert issubclass(GeminiOutputInvalid, Exception)


def test_invalid_validator_count_is_not_saved_as_partial(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([response("some text"), api_error(429)])

    with pytest.raises(GeminiKeysExhausted) as caught:
        make_client("secret-key-1", fake_clients).generate_batch(
            [page(1), page(2)], validator=lambda _text, _pages: 999
        )

    assert caught.value.final_kind == "rate_limited"
    assert caught.value.best_partial is None


# Real ModelGateway replies captured from production on 2026-09-30. The gateway
# hands each request to a marketplace seller; broken sellers answer 402/500/400
# while the next attempt (another seller) succeeds. None of them means the
# request or the key is bad.
_SELLER_OUT_OF_CREDIT = (
    "Upstream provider balance is insufficient. The upstream provider's account has "
    "insufficient credits or billing quota. This is separate from your gateway balance."
)
_SELLER_CANNOT_CONVERT = (
    "Gateway could not prepare the request. The gateway could not convert this request "
    "into the selected provider's format."
)


@pytest.mark.parametrize("code,message", [
    (402, _SELLER_OUT_OF_CREDIT),
    (500, _SELLER_CANNOT_CONVERT),
    (400, "Invalid argument"),
])
def test_gateway_seller_failures_are_retried_not_fatal(code, message):
    gateway = FakeClient([api_error(code, message=message), response(PAGE_1)])
    client = make_client(
        "",
        {"gateway-secret": gateway},
        gateway_key="gateway-secret",
        sleep=lambda _s: None,
        arabic_gemini_gateway_max_wait_seconds=1,
    )

    result = client.generate_batch([page(1)], validator=complete_validator)

    assert result.provider == "modelgateway_gemini"
    assert result.attempt_metadata[0]["failure_kind"] == "transient_http"
    assert gateway.models.calls == 2


def test_gateway_that_keeps_failing_falls_back_to_google_keys():
    gateway = FakeClient([api_error(400, message="Invalid argument")] * 40)
    google = FakeClient([response(PAGE_1)])
    client = make_client(
        "google-secret",
        {"gateway-secret": gateway, "google-secret": google},
        gateway_key="gateway-secret",
        sleep=lambda _s: None,
        arabic_gemini_gateway_max_wait_seconds=1,
    )

    result = client.generate_batch([page(1)], validator=complete_validator)

    assert result.provider != "modelgateway_gemini"
    assert google.models.calls == 1


def test_gateway_is_skipped_for_the_rest_of_a_document_after_two_failed_batches():
    # Live (2026-09-30): with every seller failing, each page waited through the
    # gateway's full retry budget before Google answered — 16 pages took 10.7
    # minutes. Two exhausted batches in a row now send the rest of the document
    # straight to the Google keys.
    gateway = FakeClient([api_error(503, message="Upstream provider unavailable")] * 200)
    google = FakeClient([response(PAGE_1)] * 4)
    client = make_client(
        "google-secret",
        {"gateway-secret": gateway, "google-secret": google},
        gateway_key="gateway-secret",
        sleep=lambda _s: None,
        arabic_gemini_gateway_max_wait_seconds=1,
    )

    client.generate_batch([page(1)], validator=complete_validator)
    client.generate_batch([page(1)], validator=complete_validator)
    calls_after_two = gateway.models.calls
    third = client.generate_batch([page(1)], validator=complete_validator)
    fourth = client.generate_batch([page(1)], validator=complete_validator)

    assert calls_after_two > 0
    assert gateway.models.calls == calls_after_two
    assert third.provider == fourth.provider != "modelgateway_gemini"
    assert google.models.calls == 4


def test_one_failed_gateway_batch_does_not_skip_it():
    # A 1 s wait budget allows 3 gateway attempts per batch (0.25 s, 0.5 s, …).
    gateway = FakeClient([api_error(503, message="Upstream provider unavailable")] * 3 + [response(PAGE_1)])
    google = FakeClient([response(PAGE_1)])
    client = make_client(
        "google-secret",
        {"gateway-secret": gateway, "google-secret": google},
        gateway_key="gateway-secret",
        sleep=lambda _s: None,
        arabic_gemini_gateway_max_wait_seconds=1,
    )

    first = client.generate_batch([page(1)], validator=complete_validator)
    second = client.generate_batch([page(1)], validator=complete_validator)

    assert first.provider != "modelgateway_gemini"
    assert second.provider == "modelgateway_gemini"
