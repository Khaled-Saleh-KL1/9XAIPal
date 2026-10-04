import base64
import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import fitz
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
from app.extraction.arabic_ocr import (
    ArabicExtractionFailed,
    extract_arabic_document,
)
from app.extraction.arabic_types import (
    ClassificationDecision,
    DocumentRoute,
    GeminiKeysExhausted,
    GeminiRequestInvalid,
    mineru_repairs_apply,
    OcrBatchResult,
    OcrUsage,
    GemmaOutputInvalid,
)
from app.extraction.gemini_ocr_client import batch_prompt


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
    prompt = payload["messages"][0]["content"]
    for instruction in (
        "rightmost column", "top to bottom", "leftmost", "Do not translate",
        "summarize", "spelling correction", "diacritics", "historical spelling",
        "headings", "lists", "tables", "displayed equations", "captions", "[غير واضح]",
        "running headers", "running footers", "bare page numbers", "Keep footnotes",
        "Do not output image links or image URLs", "caption as plain text",
    ):
        assert instruction in prompt
    assert payload["messages"][0]["images"] == [base64.b64encode(b"png-bytes").decode()]
    assert result.text == page_section(12, "عنوان")
    assert result.usage.prompt_tokens == 7
    assert result.usage.output_tokens == 9


def test_gemini_prompt_omits_running_chrome_but_keeps_footnotes():
    prompt = batch_prompt([page(1)])

    for instruction in (
        "running headers",
        "running footers",
        "bare page numbers",
        "Keep footnotes",
    ):
        assert instruction in prompt


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


def _printed_decision():
    return ClassificationDecision(
        route=DocumentRoute.ARABIC_PRINTED,
        language="arabic",
        writing_style="printed",
        text_direction="rtl",
        confidence=0.99,
        classifier_model="qwen3-vl:4b-instruct",
    )


def _handwritten_decision():
    return ClassificationDecision(
        route=DocumentRoute.ARABIC_HANDWRITTEN,
        language="arabic",
        writing_style="handwritten",
        text_direction="rtl",
        confidence=0.99,
        classifier_model="qwen3-vl:4b-instruct",
    )


def _make_pdf(path: Path, page_count: int, *, width: float = 72, height: float = 144):
    with fitz.open() as document:
        for _ in range(page_count):
            document.new_page(width=width, height=height)
        document.save(path)
    return path


def _make_pdf_with_text_layers(path: Path, page_texts):
    """Build selectable text layers without requiring a system Arabic font."""
    with fitz.open() as document:
        for text in page_texts:
            page = document.new_page(width=420, height=595)
            if text is None:
                continue
            page.insert_text((40, 60), "X")
            content_xref = page.get_contents()[0]
            content = document.xref_stream(content_xref)
            actual_text = "FEFF" + text.encode("utf-16-be").hex().upper()
            marked_content = (
                b"/Span <</ActualText <"
                + actual_text.encode("ascii")
                + b">>> BDC\n"
                + content
                + b"\nEMC"
            )
            document.update_stream(content_xref, marked_content)
        document.save(path)
    return path


@pytest.fixture
def four_page_pdf(tmp_path):
    return _make_pdf(tmp_path / "four.pdf", 4)


@pytest.fixture
def two_page_pdf(tmp_path):
    return _make_pdf(tmp_path / "two.pdf", 2)


def _gemini_batch(text, *, usage=None, latency=10, attempts=1):
    return OcrBatchResult(
        text=text,
        provider="gemini_arabic_flash",
        model="gemini-3.7-flash",
        key_index=0,
        usage=usage or OcrUsage(prompt_tokens=10, output_tokens=20, total_tokens=30),
        latency_ms=latency,
        attempt_count=attempts,
    )


class FakeGemini:
    def __init__(self, *outcomes):
        self.outcomes = deque(outcomes)
        self.requested_pages = []

    def generate_batch(self, pages, validator):
        self.requested_pages.append([page.page_number for page in pages])
        if not self.outcomes:
            raise AssertionError("unexpected Gemini batch")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeSdkModels:
    def __init__(self, outcomes, request_log, api_key):
        self.outcomes = deque(outcomes)
        self.request_log = request_log
        self.api_key = api_key

    def generate_content(self, *, model, contents, config):
        self.request_log.append({"api_key": self.api_key, "model": model})
        if not self.outcomes:
            raise AssertionError("unexpected extra Gemini provider request")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeSdkClient:
    def __init__(self, outcomes, request_log, api_key):
        self.models = FakeSdkModels(outcomes, request_log, api_key)

    def close(self):
        pass


def sdk_response(text):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=2,
            candidates_token_count=3,
            thoughts_token_count=0,
            total_token_count=5,
        ),
    )


def sdk_api_error(code, message):
    from google.genai import errors

    return errors.APIError(
        code=code,
        response_json={"error": {"code": code, "message": message, "details": []}},
        response=httpx.Response(
            status_code=code,
            request=httpx.Request("POST", "https://provider.invalid"),
        ),
    )


class FakeGemma:
    def __init__(self, replies):
        self.replies = replies
        self.requested_pages = []
        self.requested_styles = []
        self.handwritten_opt_ins = []

    def generate_page(
        self, page, *, writing_style="printed", allow_handwritten=False
    ):
        self.requested_pages.append(page.page_number)
        self.requested_styles.append(writing_style)
        self.handwritten_opt_ins.append(allow_handwritten)
        outcome = self.replies[page.page_number]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, OcrBatchResult):
            return outcome
        return OcrBatchResult(
            text=page_section(page.page_number, outcome),
            provider="gemma4_arabic_fallback",
            model="gemma4:31b-cloud",
            key_index=0,
            usage=OcrUsage(prompt_tokens=3, output_tokens=4, total_tokens=7),
            latency_ms=5,
        )


def extract(
    pdf_path,
    output_dir,
    gemini,
    gemma,
    *,
    progress_callback=None,
    settings=None,
    classification=None,
):
    settings = settings or Settings(
        arabic_ocr_enabled=True,
        arabic_ocr_dpi=200,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
    )
    return extract_arabic_document(
        pdf_path=pdf_path,
        output_dir=output_dir,
        classification=classification or _printed_decision(),
        gemini_client=gemini,
        gemma_client=gemma,
        settings=settings,
        progress_callback=progress_callback,
    )


def test_gateway_provider_is_recorded_on_each_page_and_in_summary(tmp_path):
    from app.extraction.gemini_ocr_client import GeminiOcrClient

    pdf = _make_pdf(tmp_path / "gateway.pdf", 1)
    settings = Settings(
        _env_file=None,
        modelgateway_api_key="gateway-secret",
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
    )
    request_log = []
    clients = {
        "gateway-secret": FakeSdkClient(
            [sdk_response(page_section(1, "نص بوابة"))], request_log, "gateway"
        )
    }
    result = extract(
        pdf,
        tmp_path / "gateway-out",
        GeminiOcrClient(
            settings=settings,
            client_factory=lambda key: clients[key],
            sleep=lambda _seconds: None,
        ),
        FakeGemma({}),
        settings=settings,
    )

    content = json.loads((result.output_dir / "content_list.json").read_text())
    raw_pages = json.loads((result.output_dir / "raw_pages.json").read_text())
    assert result.extractor == "gemini_arabic_flash"
    assert result.provider_summary == [
        {"provider": "modelgateway_gemini", "pages": [1]}
    ]
    assert content[0]["ocr_provider"] == "modelgateway_gemini"
    assert raw_pages[0]["provider"] == "modelgateway_gemini"
    assert raw_pages[0]["model"] == "gemini-3.8-flash"
    assert request_log == [{"api_key": "gateway", "model": "gemini-3.8-flash"}]


def test_handwritten_route_uses_gateway_then_google_pro_then_gemma(tmp_path):
    from app.extraction.gemini_ocr_client import GeminiOcrClient

    pdf = _make_pdf(tmp_path / "handwritten.pdf", 1)
    settings = Settings(
        _env_file=None,
        modelgateway_api_key="gateway-secret",
        gemini_api_keys_raw="google-secret",
        arabic_gemini_gateway_max_wait_seconds=0,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
    )
    request_log = []
    clients = {
        "gateway-secret": FakeSdkClient(
            [sdk_api_error(403, "Upstream provider access denied")],
            request_log,
            "gateway",
        ),
        "google-secret": FakeSdkClient(
            [sdk_api_error(403, "API key not valid")], request_log, "google"
        ),
    }
    gemma = FakeGemma({1: "هذا نص بخط اليد"})

    result = extract(
        pdf,
        tmp_path / "handwritten-out",
        GeminiOcrClient(
            settings=settings,
            client_factory=lambda key: clients[key],
            writing_style="handwritten",
            sleep=lambda _seconds: None,
        ),
        gemma,
        settings=settings,
        classification=_handwritten_decision(),
    )

    assert [request["model"] for request in request_log] == [
        "gemini-3.1-pro",
        "gemini-3.1-pro-preview",
    ]
    assert [request["api_key"] for request in request_log] == ["gateway", "google"]
    assert result.pages[0].provider == "gemma4_arabic_fallback"
    assert gemma.requested_styles == ["handwritten"]
    assert gemma.handwritten_opt_ins == [True]
    assert result.provider_summary == [
        {"provider": "gemma4_arabic_fallback", "pages": [1]}
    ]


def test_truncated_batch_keeps_complete_gemini_prefix(four_page_pdf, tmp_path):
    partial = _gemini_batch(
        page_section(1, "واحد")
        + "\n"
        + page_section(2, "اثنان")
        + "\n<!-- PAGE:3 -->\nمبتور"
    )
    gemini = FakeGemini(
        GeminiKeysExhausted(
            "daily_quota", best_partial=partial, attempt_usage=(partial.usage,)
        )
    )
    gemma = FakeGemma({3: "ن\x00ص", 4: "أربعة"})

    result = extract(four_page_pdf, tmp_path / "out", gemini, gemma)

    assert result.extractor == "gemini_gemma_arabic_hybrid"
    assert result.provider_summary == [
        {"provider": "gemini_arabic_flash", "pages": [1, 2]},
        {"provider": "gemma4_arabic_fallback", "pages": [3, 4]},
    ]
    assert gemma.requested_pages == [3, 4]
    assert [page.page_number for page in result.pages] == [1, 2, 3, 4]
    assert result.usage == OcrUsage(
        prompt_tokens=16,
        output_tokens=28,
        total_tokens=44,
    )
    assert result.pages[2].raw_markdown == "ن\x00ص"
    assert result.pages[2].markdown == "نص"
    assert result.pages[2].repaired is True
    assert result.pages[3].repaired is False
    content = json.loads((result.output_dir / "content_list.json").read_text(encoding="utf-8"))
    assert [block["ocr_repaired"] for block in content] == [False, False, True, False]


def test_failed_final_page_never_publishes_partial_output(two_page_pdf, tmp_path):
    target = tmp_path / "out"
    gemini = FakeGemini(GeminiKeysExhausted("daily_quota", None, ()))
    gemma = FakeGemma(
        {
            1: "واحد",
            2: GemmaKeysExhausted("provider_error", ()),
        }
    )

    with pytest.raises(ArabicExtractionFailed):
        extract(two_page_pdf, target, gemini, gemma)

    assert not target.exists()


def test_gemma_repair_invalid_output_retries_page_then_commits(tmp_path, monkeypatch):
    from app.extraction import arabic_ocr

    pdf = _make_pdf(tmp_path / "one.pdf", 1)
    original_repair = arabic_ocr.repair_gemma_page
    repair_calls = []

    def repair(page, source_text):
        repair_calls.append(page.page_number)
        if len(repair_calls) == 1:
            raise GemmaOutputInvalid("repetition found after parsing")
        return original_repair(page, source_text)

    monkeypatch.setattr(arabic_ocr, "repair_gemma_page", repair)
    gemma = FakeGemma({1: "هذا النص العربي واضح"})

    result = extract(
        pdf,
        tmp_path / "out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        gemma,
    )

    assert repair_calls == [1, 1]
    assert gemma.requested_pages == [1, 1]
    assert result.pages[0].markdown == "هذا النص العربي واضح"


def test_repeated_gemma_repair_invalid_output_is_typed_retryable_failure(
    tmp_path, monkeypatch
):
    from app.extraction import arabic_ocr

    pdf = _make_pdf(tmp_path / "one.pdf", 1)
    def repair_fails(_page, source_text):
        raise GemmaOutputInvalid("repetition found after parsing")

    monkeypatch.setattr(arabic_ocr, "repair_gemma_page", repair_fails)
    target = tmp_path / "out"
    gemma = FakeGemma({1: "هذا النص العربي واضح"})

    with pytest.raises(ArabicExtractionFailed) as raised:
        extract(
            pdf,
            target,
            FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
            gemma,
        )

    assert raised.value.failure_kind == "invalid_output"
    assert raised.value.retryable is True
    assert gemma.requested_pages == [1, 1]
    assert not target.exists()


def test_document_at_threshold_uses_one_gemini_request(four_page_pdf, tmp_path):
    response_text = "\n".join(page_section(i, f"صفحة {i}") for i in range(1, 5))
    gemini = FakeGemini(_gemini_batch(response_text))
    gemma = FakeGemma({})

    result = extract(four_page_pdf, tmp_path / "out", gemini, gemma)

    assert result.extractor == "gemini_arabic_flash"
    assert gemini.requested_pages == [[1, 2, 3, 4]]
    assert gemma.requested_pages == []


def test_gemma_only_result_reports_gemma_extractor(tmp_path):
    pdf = _make_pdf(tmp_path / "one.pdf", 1)
    result = extract(
        pdf,
        tmp_path / "out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        FakeGemma({1: "نص عربي مطبوع"}),
    )

    assert result.extractor == "gemma4_arabic_fallback"


def test_large_document_uses_consecutive_four_page_batches(tmp_path):
    pdf = _make_pdf(tmp_path / "five.pdf", 5)
    first = "\n".join(page_section(i, f"صفحة {i}") for i in range(1, 5))
    second = page_section(5, "صفحة خمسة")
    gemini = FakeGemini(_gemini_batch(first), _gemini_batch(second))

    result = extract(pdf, tmp_path / "out", gemini, FakeGemma({}))

    assert gemini.requested_pages == [[1, 2, 3, 4], [5]]
    assert [page.page_number for page in result.pages] == [1, 2, 3, 4, 5]


def test_incomplete_page_is_reprocessed_whole_by_gemma(two_page_pdf, tmp_path):
    partial = _gemini_batch(page_section(1, "واحد") + "\n<!-- PAGE:2 -->\nمبتور")
    gemini = FakeGemini(
        GeminiKeysExhausted("invalid_output", partial, (partial.usage,))
    )
    gemma = FakeGemma({2: "اثنان"})

    result = extract(two_page_pdf, tmp_path / "out", gemini, gemma)

    assert gemma.requested_pages == [2]
    assert [(page.page_number, page.provider) for page in result.pages] == [
        (1, "gemini_arabic_flash"),
        (2, "gemma4_arabic_fallback"),
    ]


def test_an_entirely_invalid_gemini_response_is_retried_once(two_page_pdf, tmp_path):
    valid = "\n".join(page_section(i, f"صفحة {i}") for i in range(1, 3))
    gemini = FakeGemini(_gemini_batch("not page marked"), _gemini_batch(valid))
    gemma = FakeGemma({})

    result = extract(two_page_pdf, tmp_path / "out", gemini, gemma)

    assert gemini.requested_pages == [[1, 2], [1, 2]]
    assert gemma.requested_pages == []
    manifest = json.loads((result.output_dir / "ocr_manifest.json").read_text(encoding="utf-8"))
    assert [run["retry_count"] for run in manifest["provider_runs"]] == [0, 1]


def test_progress_reports_each_committed_page(two_page_pdf, tmp_path):
    valid = "\n".join(page_section(i, f"صفحة {i}") for i in range(1, 3))
    progress = []

    extract(
        two_page_pdf,
        tmp_path / "out",
        FakeGemini(_gemini_batch(valid)),
        FakeGemma({}),
        progress_callback=lambda completed, total: progress.append((completed, total)),
    )

    assert progress == [(1, 2), (2, 2)]


def test_rendering_is_rgb_at_configured_200_dpi(two_page_pdf, tmp_path):
    response_text = "\n".join(page_section(i, f"صفحة {i}") for i in range(1, 3))
    gemini = FakeGemini(_gemini_batch(response_text))
    inspected = []

    class InspectGemini(FakeGemini):
        def generate_batch(self, pages, validator):
            for page in pages:
                pixmap = fitz.Pixmap(page.png)
                inspected.append((pixmap.width, pixmap.height, pixmap.n, pixmap.alpha))
            return super().generate_batch(pages, validator)

    extract(two_page_pdf, tmp_path / "out", InspectGemini(_gemini_batch(response_text)), FakeGemma({}))

    assert inspected == [(200, 400, 3, 0), (200, 400, 3, 0)]


def test_artifacts_have_contiguous_pages_provenance_and_no_credentials(two_page_pdf, tmp_path):
    valid = "\n".join(page_section(i, f"صفحة {i}") for i in range(1, 3))
    target = tmp_path / "out"

    extract(two_page_pdf, target, FakeGemini(_gemini_batch(valid)), FakeGemma({}))

    content = json.loads((target / "content_list.json").read_text(encoding="utf-8"))
    manifest = json.loads((target / "ocr_manifest.json").read_text(encoding="utf-8"))
    raw_pages = json.loads((target / "raw_pages.json").read_text(encoding="utf-8"))
    assert [block["page_idx"] for block in content] == [0, 1]
    assert [page["page_number"] for page in raw_pages] == [1, 2]
    assert manifest["classification"]["writing_style"] == "printed"
    manifest_text = json.dumps(manifest).lower()
    assert "api_key" not in manifest_text
    assert "transcribe every visible page" not in manifest_text
    assert "ocr_prompt" not in manifest_text
    assert (target / "document.md").exists()


def test_request_schema_failure_does_not_activate_gemma(two_page_pdf, tmp_path):
    gemini = FakeGemini(GeminiRequestInvalid("invalid request"))
    gemma = FakeGemma({1: "واحد", 2: "اثنان"})

    with pytest.raises(ArabicExtractionFailed):
        extract(two_page_pdf, tmp_path / "out", gemini, gemma)

    assert gemma.requested_pages == []


def test_failed_replace_rolls_back_existing_artifact_directory(two_page_pdf, tmp_path, monkeypatch):
    import app.extraction.arabic_ocr as arabic_ocr

    target = tmp_path / "out"
    target.mkdir()
    (target / "old.txt").write_text("previous complete output", encoding="utf-8")
    valid = "\n".join(page_section(i, f"صفحة {i}") for i in range(1, 3))
    real_replace = arabic_ocr.os.replace

    def fail_staging_promotion(source, destination):
        if ".staging-" in str(source) and Path(destination) == target:
            raise OSError("mock promotion failure")
        return real_replace(source, destination)

    monkeypatch.setattr(arabic_ocr.os, "replace", fail_staging_promotion)

    with pytest.raises(ArabicExtractionFailed):
        extract(two_page_pdf, target, FakeGemini(_gemini_batch(valid)), FakeGemma({}))

    assert (target / "old.txt").read_text(encoding="utf-8") == "previous complete output"
    assert not list(tmp_path.glob(".out.staging-*"))


# Live (2026-09-30): a 78-page Arabic book OCR'd pages 1–38 cleanly through
# Gemma, then Gemma looped to its 32,768-token cap on page 39 on every key and
# the whole document failed. Up to 10% of a document's pages (rounded down)
# may now come back unreadable; they carry a visible Arabic marker instead.
def test_one_unreadable_page_in_a_long_document_is_marked_not_fatal(tmp_path):
    from app.extraction.arabic_ocr import UNREADABLE_PAGE_MARKER

    pdf = _make_pdf(tmp_path / "twenty.pdf", 20)
    replies = {n: f"صفحة رقم {n}" for n in range(1, 21)}
    replies[7] = GemmaKeysExhausted("invalid_output", ())
    gemma = FakeGemma(replies)

    result = extract(pdf, tmp_path / "out", FakeGemini(GeminiKeysExhausted("daily_quota", None, ())), gemma)

    assert [p.page_number for p in result.pages] == list(range(1, 21))
    assert result.pages[6].markdown == UNREADABLE_PAGE_MARKER
    assert result.pages[7].markdown == "صفحة رقم 8"


def test_english_pdf_text_layer_recovers_page_and_records_its_provider(tmp_path):
    from app.extraction.arabic_ocr import UNREADABLE_PAGE_MARKER

    text = (
        "English selectable text supports recovery when every OCR provider fails.\n"
        "This second line contains enough body text to distinguish it from a heading.\n"
        "A final sentence confirms that the PDF page has a useful text layer."
    )
    pdf = _make_pdf_with_text_layers(tmp_path / "english-text-layer.pdf", [text])
    settings = Settings(
        _env_file=None,
        arabic_ocr_enabled=True,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
        arabic_ocr_max_unreadable_page_share=0.0,
    )

    result = extract(
        pdf,
        tmp_path / "english-text-layer-out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        FakeGemma({1: GemmaKeysExhausted("provider_error", ())}),
        settings=settings,
    )

    assert result.pages[0].markdown == text
    assert result.pages[0].provider == "pdf_text_layer"
    assert result.provider_summary == [
        {"provider": "pdf_text_layer", "pages": [1]}
    ]
    assert result.extractor == "pdf_text_layer"
    assert mineru_repairs_apply(result.extractor) is False
    raw_pages = json.loads(
        (result.output_dir / "raw_pages.json").read_text(encoding="utf-8")
    )
    assert raw_pages[0]["provider"] == "pdf_text_layer"
    assert raw_pages[0]["markdown"] == text
    assert result.pages[0].markdown != UNREADABLE_PAGE_MARKER


@pytest.mark.parametrize(
    "sparse_text",
    ["23", "Header text from a running section"],
    ids=("page-number", "header-only"),
)
def test_sparse_page_number_or_header_text_layer_keeps_unreadable_marker(
    tmp_path, sparse_text
):
    from app.extraction.arabic_ocr import UNREADABLE_PAGE_MARKER

    pdf = _make_pdf_with_text_layers(tmp_path / "sparse-text-layer.pdf", [sparse_text])
    settings = Settings(
        _env_file=None,
        arabic_ocr_enabled=True,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
        arabic_ocr_max_unreadable_page_share=1.0,
    )

    result = extract(
        pdf,
        tmp_path / "page-number-out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        FakeGemma({1: GemmaKeysExhausted("provider_error", ())}),
        settings=settings,
    )

    assert result.pages[0].markdown == UNREADABLE_PAGE_MARKER
    assert result.pages[0].provider == "gemma4_arabic_fallback"


@pytest.mark.parametrize(
    "bad_text",
    [
        "ﻫﺬﺍ ﺍﻟﻨﺺ ﺍﻟﻌﺮﺑﻲ ﻭﺍﺿﺢ ﻭﻣﻘﺮﻭﺀ ﻣﻦ "
        "ﺍﻟﺠﻤﻴﻊ\n"
        "ﻧﺴﺘﺨﺪﻡ ﻫﺬﻩ ﺍﻟﺼﻔﺤﺔ ﻟﻠﺘﺤﻘﻖ ﻣﻦ "
        "ﺟﻮﺩﺓ ﺍﻟﻨﺺ\n"
        "ﺍﻟﻤﺴﺘﺨﺮﺝ ﻋﻨﺪ ﻓﺸﻞ ﻣﺰﻭﺩﻱ ﺍﻟﺘﻌﺮﻑ "
        "ﺍﻟﻀﻮﺋﻲ",
        "\n".join(
            " ".join(word[::-1] for word in line.split())
            for line in (
                "هذا النص العربي واضح ومقروء من الجميع",
                "نستخدم هذه الصفحة للتحقق من جودة النص",
                "المستخرج عند فشل مزودي التعرف الضوئي",
            )
        ),
    ],
    ids=("presentation-forms", "reversed-arabic"),
)
def test_broken_arabic_text_layer_keeps_unreadable_marker(tmp_path, bad_text):
    from app.extraction.arabic_ocr import UNREADABLE_PAGE_MARKER

    pdf = _make_pdf_with_text_layers(tmp_path / "broken-arabic.pdf", [bad_text])
    settings = Settings(
        _env_file=None,
        arabic_ocr_enabled=True,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
        arabic_ocr_max_unreadable_page_share=1.0,
    )

    result = extract(
        pdf,
        tmp_path / "broken-arabic-out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        FakeGemma({1: GemmaKeysExhausted("provider_error", ())}),
        settings=settings,
    )

    assert result.pages[0].markdown == UNREADABLE_PAGE_MARKER
    assert result.pages[0].provider == "gemma4_arabic_fallback"


def test_mojibake_arabic_text_layer_keeps_unreadable_marker(tmp_path):
    from app.extraction.arabic_ocr import UNREADABLE_PAGE_MARKER

    english_text = (
        "English selectable text supports recovery when every OCR provider fails.\n"
        "This second line contains enough body text to distinguish it from a heading.\n"
        "A final sentence confirms that the PDF page has a useful text layer."
    )
    corrupted_arabic = (
        "هذا النص العربي واضح ومقروء من الجميع. "
        "نستخدم هذه الصفحة للتحقق من جودة النص المستخرج."
    ).encode("utf-8").decode("latin-1")
    pdf = _make_pdf_with_text_layers(
        tmp_path / "mojibake-arabic.pdf", [english_text + "\n" + corrupted_arabic]
    )
    settings = Settings(
        _env_file=None,
        arabic_ocr_enabled=True,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
        arabic_ocr_max_unreadable_page_share=1.0,
    )

    result = extract(
        pdf,
        tmp_path / "mojibake-arabic-out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        FakeGemma({1: GemmaKeysExhausted("provider_error", ())}),
        settings=settings,
    )

    assert result.pages[0].markdown == UNREADABLE_PAGE_MARKER
    assert result.pages[0].provider == "gemma4_arabic_fallback"


def test_good_arabic_pdf_text_layer_recovers_page(tmp_path):
    text = (
        "هذا النص العربي واضح ومقروء من الجميع\n"
        "نستخدم هذه الصفحة للتحقق من جودة النص\n"
        "المستخرج عند فشل مزودي التعرف الضوئي"
    )
    pdf = _make_pdf_with_text_layers(tmp_path / "good-arabic-text-layer.pdf", [text])
    settings = Settings(
        _env_file=None,
        arabic_ocr_enabled=True,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
        arabic_ocr_max_unreadable_page_share=0.0,
    )

    result = extract(
        pdf,
        tmp_path / "good-arabic-text-layer-out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        FakeGemma({1: GemmaKeysExhausted("provider_error", ())}),
        settings=settings,
    )

    assert result.pages[0].markdown == text
    assert result.pages[0].provider == "pdf_text_layer"
    assert result.provider_summary == [
        {"provider": "pdf_text_layer", "pages": [1]}
    ]


def test_mostly_english_text_layer_survives_a_small_presentation_form_fragment(
    tmp_path,
):
    text = (
        "English selectable text supports recovery when every OCR provider fails.\n"
        "This second line contains enough body text to distinguish it from a heading.\n"
        "A final sentence confirms that the PDF page has a useful text layer.\n"
        "Arabic example: ﻣﺮﺣﺒﺎ"
    )
    pdf = _make_pdf_with_text_layers(tmp_path / "mixed-text-layer.pdf", [text])
    settings = Settings(
        _env_file=None,
        arabic_ocr_enabled=True,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
        arabic_ocr_max_unreadable_page_share=0.0,
    )

    result = extract(
        pdf,
        tmp_path / "mixed-text-layer-out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        FakeGemma({1: GemmaKeysExhausted("provider_error", ())}),
        settings=settings,
    )

    assert result.pages[0].markdown == text
    assert result.pages[0].provider == "pdf_text_layer"


def test_pdf_text_layer_pages_do_not_consume_unreadable_budget(tmp_path):
    from app.extraction.arabic_ocr import UNREADABLE_PAGE_MARKER

    useful_text = (
        "English selectable text supports recovery when every OCR provider fails.\n"
        "This second line contains enough body text to distinguish it from a heading.\n"
        "A final sentence confirms that the PDF page has a useful text layer."
    )
    page_texts = [useful_text, useful_text] + [None] * 18
    pdf = _make_pdf_with_text_layers(tmp_path / "text-layer-budget.pdf", page_texts)
    settings = Settings(
        _env_file=None,
        arabic_ocr_enabled=True,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_batch_pages=4,
        arabic_ocr_max_unreadable_page_share=0.10,
    )
    replies = {number: f"صفحة رقم {number}" for number in range(1, 21)}
    for number in (1, 2, 3):
        replies[number] = GemmaKeysExhausted("provider_error", ())

    result = extract(
        pdf,
        tmp_path / "text-layer-budget-out",
        FakeGemini(GeminiKeysExhausted("daily_quota", None, ())),
        FakeGemma(replies),
        settings=settings,
    )

    assert result.pages[0].provider == "pdf_text_layer"
    assert result.pages[1].provider == "pdf_text_layer"
    assert result.pages[2].markdown == UNREADABLE_PAGE_MARKER
    assert result.extractor == "arabic_pdf_text_layer_hybrid"
    assert result.provider_summary[:2] == [
        {"provider": "pdf_text_layer", "pages": [1, 2]},
        {"provider": "gemma4_arabic_fallback", "pages": list(range(3, 21))},
    ]


def test_too_many_unreadable_pages_still_fail_the_document(tmp_path):
    pdf = _make_pdf(tmp_path / "twenty.pdf", 20)
    replies = {n: f"صفحة رقم {n}" for n in range(1, 21)}
    for n in (3, 5, 7):  # 3 > floor(20 * 10%) = 2
        replies[n] = GemmaKeysExhausted("invalid_output", ())
    target = tmp_path / "out"

    with pytest.raises(ArabicExtractionFailed):
        extract(pdf, target, FakeGemini(GeminiKeysExhausted("daily_quota", None, ())), FakeGemma(replies))

    assert not target.exists()


def test_gemma_page_output_cap_is_its_own_setting(fake_http):
    from app.core.config import Settings

    assert Settings().arabic_gemma_max_output_tokens == 8192
