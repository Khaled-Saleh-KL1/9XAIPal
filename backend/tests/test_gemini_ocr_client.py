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


def api_error(code: int) -> errors.APIError:
    return errors.APIError(
        code=code,
        response_json={"error": {"code": code, "message": "mock failure"}},
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

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        self.last_model = model
        self.last_contents = contents
        self.last_config = config
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


def make_client(keys: str, fake_clients, *, sleep=lambda _seconds: None):
    from app.core.config import Settings

    settings = Settings(
        gemini_api_keys_raw=keys,
        arabic_gemini_printed_model="gemini-test-flash",
        arabic_ocr_max_output_tokens=1234,
    )
    return GeminiOcrClient(
        settings=settings,
        client_factory=lambda api_key: fake_clients[api_key],
        sleep=sleep,
        jitter=lambda _low, _high: 0,
    )


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


def test_request_uses_low_thinking_high_resolution_and_page_image_parts(fake_clients):
    fake_clients["secret-key-1"] = FakeClient([response(f"{PAGE_1}\n{PAGE_2}")])
    make_client("secret-key-1", fake_clients).generate_batch(
        [page(1), page(2)], validator=complete_validator
    )
    models = fake_clients["secret-key-1"].models

    assert models.last_model == "gemini-test-flash"
    assert models.last_config.max_output_tokens == 1234
    assert models.last_config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert models.last_config.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_HIGH
    assert [part.text for part in models.last_contents if part.text] == [
        "Transcribe every visible page verbatim in its original language. Preserve reading order, paragraphs, headings, and tables in Markdown. For Arabic or mixed Arabic/English pages, preserve logical RTL reading order; keep English spans and numerals in their original order. Do not translate, summarize, infer, or repair text. Mark illegible text as [غير واضح]. Wrap each page exactly with <!-- PAGE:n --> and <!-- END_PAGE:n --> markers, replacing n with that page's number shown before its image.",
        "PAGE 1",
        "PAGE 2",
    ]
    image_parts = [part for part in models.last_contents if part.inline_data]
    assert len(image_parts) == 2
    assert all(part.inline_data.mime_type == "image/png" for part in image_parts)


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
