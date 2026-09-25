import base64
from collections import deque

import httpx
import pytest

from app.core.config import Settings
from app.extraction.arabic_fallback import (
    GemmaArabicFallback,
    GemmaKeysExhausted,
    GemmaRequestInvalid,
    HandwrittenFallbackForbidden,
)
from app.extraction.arabic_types import RenderedPage


def page_section(number: int, text: str) -> str:
    return f"<!-- PAGE:{number} -->\n{text}\n<!-- END_PAGE:{number} -->"


class FakeHttpClient:
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.requests = []
        self.closed = False

    def post(self, url, *, json, headers, timeout):
        self.requests.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self.outcomes:
            raise AssertionError("unexpected extra Ollama request")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        status, content = outcome
        response = httpx.Response(
            status_code=status,
            json={
                "message": {"content": content},
                "prompt_eval_count": 7,
                "eval_count": 9,
            },
            request=httpx.Request("POST", url),
        )
        response.raise_for_status()
        return response

    def close(self):
        self.closed = True


class FakeHttp:
    def __init__(self):
        self.clients = {}
        self.keys_called = []

    def key(self, name, outcomes):
        self.clients[name] = FakeHttpClient(outcomes)
        return self

    def client_factory(self, api_key):
        self.keys_called.append(api_key)
        return self.clients[api_key]

    @property
    def calls(self):
        return sum(len(client.requests) for client in self.clients.values())


def make_fallback(keys: str, fake_http: FakeHttp):
    settings = Settings(
        arabic_gemma_api_keys_raw=keys,
        arabic_gemma_base_url="https://ollama.example",
        arabic_gemma_fallback_model="gemma4:31b-cloud",
        arabic_ocr_max_output_tokens=1234,
    )
    return GemmaArabicFallback(settings=settings, http_client_factory=fake_http.client_factory)


def test_fallback_rotates_only_ollama_keys(fake_http):
    fake_http.key("ollama-one", [(429, "quota")]).key(
        "ollama-two", [(200, page_section(7, "نص"))]
    )

    result = make_fallback("ollama-one,ollama-two", fake_http).generate_page(page(7))

    assert result.provider == "gemma4_arabic_fallback"
    assert result.key_index == 1
    assert fake_http.keys_called == ["ollama-one", "ollama-two"]


def page(number: int) -> RenderedPage:
    return RenderedPage(page_number=number, png=b"png-bytes")


def test_fallback_rejects_handwritten_input_before_any_network_call():
    fake_http = FakeHttp().key("ollama-one", [])
    fallback = make_fallback("ollama-one", fake_http)

    with pytest.raises(HandwrittenFallbackForbidden):
        fallback.generate_page(page(1), writing_style="handwritten")

    assert fake_http.calls == 0
    assert fake_http.keys_called == []


@pytest.mark.parametrize("writing_style", ["mixed", "unknown", "uncertain"])
def test_fallback_rejects_any_style_not_confidently_printed(writing_style):
    fake_http = FakeHttp().key("ollama-one", [])

    with pytest.raises(HandwrittenFallbackForbidden):
        make_fallback("ollama-one", fake_http).generate_page(
            page(1), writing_style=writing_style
        )

    assert fake_http.calls == 0


def test_fallback_request_is_direct_ollama_chat_with_absolute_page_and_png():
    fake_http = FakeHttp().key("ollama-one", [(200, page_section(12, "عنوان"))])

    result = make_fallback("ollama-one", fake_http).generate_page(page(12))

    request = fake_http.clients["ollama-one"].requests[0]
    assert request["url"] == "https://ollama.example/api/chat"
    assert request["headers"] == {"Authorization": "Bearer ollama-one"}
    assert request["timeout"].read == 600
    payload = request["json"]
    assert payload["model"] == "gemma4:31b-cloud"
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0
    assert payload["options"]["num_predict"] == 1234
    assert "PAGE:12" in payload["messages"][0]["content"]
    assert payload["messages"][0]["images"] == [base64.b64encode(b"png-bytes").decode()]
    assert result.text == page_section(12, "عنوان")
    assert result.usage.prompt_tokens == 7
    assert result.usage.output_tokens == 9


@pytest.mark.parametrize("status", [401, 403, 408, 429, 500, 503])
def test_provider_auth_quota_and_transient_statuses_rotate(status):
    fake_http = FakeHttp().key("ollama-one", [(status, "failure")]).key(
        "ollama-two", [(200, page_section(1, "نص"))]
    )

    result = make_fallback("ollama-one,ollama-two", fake_http).generate_page(page(1))

    assert result.key_index == 1
    assert fake_http.keys_called == ["ollama-one", "ollama-two"]


def test_network_failure_rotates_to_next_ollama_key():
    fake_http = FakeHttp().key(
        "ollama-one", [httpx.ConnectError("mock network error")]
    ).key("ollama-two", [(200, page_section(1, "نص"))])

    result = make_fallback("ollama-one,ollama-two", fake_http).generate_page(page(1))

    assert result.key_index == 1


def test_timeout_rotates_to_next_ollama_key():
    fake_http = FakeHttp().key(
        "ollama-one", [httpx.ReadTimeout("mock read timeout")]
    ).key("ollama-two", [(200, page_section(1, "نص"))])

    result = make_fallback("ollama-one,ollama-two", fake_http).generate_page(page(1))

    assert result.key_index == 1


def test_bad_request_does_not_rotate():
    fake_http = FakeHttp().key("ollama-one", [(400, "bad request")]).key(
        "ollama-two", [(200, page_section(1, "نص"))]
    )

    with pytest.raises(GemmaRequestInvalid):
        make_fallback("ollama-one,ollama-two", fake_http).generate_page(page(1))

    assert fake_http.clients["ollama-two"].requests == []


def test_invalid_output_retries_once_and_never_logs_secrets(caplog):
    fake_http = FakeHttp().key(
        "ollama-secret", [(200, "empty"), (200, page_section(1, "نص"))]
    )

    result = make_fallback("ollama-secret", fake_http).generate_page(page(1))

    assert result.text == page_section(1, "نص")
    assert fake_http.calls == 2
    assert "ollama-secret" not in caplog.text


def test_all_invalid_output_raises_without_leaking_keys():
    fake_http = FakeHttp().key("ollama-secret", [(200, "bad"), (200, "still bad")])

    with pytest.raises(GemmaKeysExhausted) as caught:
        make_fallback("ollama-secret", fake_http).generate_page(page(1))

    assert caught.value.final_kind == "invalid_output"
    assert "ollama-secret" not in str(caught.value)


def test_keyless_local_ollama_is_supported():
    fake_http = FakeHttp().key(None, [(200, page_section(1, "نص"))])
    settings = Settings(
        arabic_gemma_api_keys_raw="",
        ollama_api_key="",
        arabic_gemma_base_url="http://localhost:11434",
    )
    fallback = GemmaArabicFallback(settings=settings, http_client_factory=fake_http.client_factory)

    result = fallback.generate_page(page(1))

    assert result.key_index is None
    assert fake_http.clients[None].requests[0]["headers"] == {}


@pytest.fixture
def fake_http():
    return FakeHttp()
