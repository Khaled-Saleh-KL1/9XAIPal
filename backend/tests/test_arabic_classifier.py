import base64
import json
from types import SimpleNamespace

import fitz
import httpx
import pytest

from app.extraction.arabic_types import (
    DocumentRoute,
    PageStyleVote,
)
from app.extraction import arabic_classifier
from app.extraction.arabic_classifier import (
    ArabicClassifierResponseInvalid,
    ClassificationImage,
    DocumentTextEvidence,
    TextPageEvidence,
    classify_document,
    call_local_router,
)


def _make_pdf(tmp_path, name, texts=("",)):
    path = tmp_path / name
    doc = fitz.open()
    for text in texts:
        page = doc.new_page(width=420, height=595)
        if text:
            page.insert_text((32, 60), text)
        else:
            page.draw_rect(fitz.Rect(55, 70, 365, 525), color=(0, 0, 0), width=3)
            page.draw_line((70, 110), (350, 110), color=(0, 0, 0), width=2)
    doc.save(path)
    doc.close()
    return path


def _make_scanned_pdf(tmp_path, name, page_count, *, blank_pages=()):
    path = tmp_path / name
    blank_pages = set(blank_pages)
    document = fitz.open()
    for page_number in range(1, page_count + 1):
        page = document.new_page(width=420, height=595)
        if page_number not in blank_pages:
            page.draw_rect(fitz.Rect(55, 70, 365, 525), color=(0, 0, 0), width=3)
            page.draw_line((70, 110), (350, 110), color=(0, 0, 0), width=2)
            page.draw_line((70, 145), (340, 145), color=(0, 0, 0), width=2)
    document.save(path)
    document.close()
    return path


@pytest.fixture
def english_only_pdf(tmp_path):
    return _make_pdf(tmp_path, "english.pdf", ("This is an English document with enough body text.",))


@pytest.fixture
def english_text_with_embedded_image_pdf(tmp_path):
    path = tmp_path / "english-text-with-image.pdf"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    pixmap.clear_with(255)
    image_bytes = pixmap.tobytes("png")
    doc = fitz.open()
    page = doc.new_page(width=420, height=595)
    page.insert_text(
        (32, 60),
        "English selectable text layer with enough words to be a document body. " * 4,
    )
    page.insert_image(fitz.Rect(120, 120, 240, 240), stream=image_bytes)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def english_cover_with_scanned_page_pdf(tmp_path):
    path = tmp_path / "english-cover-with-scanned-page.pdf"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    pixmap.clear_with(255)
    image_bytes = pixmap.tobytes("png")
    doc = fitz.open()
    cover = doc.new_page(width=420, height=595)
    cover.insert_text(
        (32, 60),
        "English cover page with enough selectable body text to identify the language.",
    )
    scan = doc.new_page(width=420, height=595)
    scan.insert_image(fitz.Rect(24, 24, 396, 571), stream=image_bytes)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def mixed_text_pdf(tmp_path):
    return _make_pdf(tmp_path, "mixed.pdf", ("A mostly English document with Arabic body.",))


@pytest.fixture
def scanned_arabic_pdf(tmp_path):
    return _make_pdf(tmp_path, "scanned-arabic.pdf")


@pytest.fixture
def decorative_printed_pdf(tmp_path):
    return _make_pdf(tmp_path, "decorative-print.pdf")


@pytest.fixture
def one_page_scan(tmp_path):
    return _make_pdf(tmp_path, "one-page-scan.pdf")


@pytest.fixture
def confident_handwriting_pdf(tmp_path):
    return _make_pdf(tmp_path, "handwriting.pdf")


@pytest.fixture
def printed_form_with_signature_pdf(tmp_path):
    return _make_pdf(tmp_path, "form-signature.pdf")


@pytest.fixture
def two_page_scan(tmp_path):
    return _make_pdf(tmp_path, "two-page-scan.pdf", ("", ""))


@pytest.fixture
def no_readable_text_pdf(tmp_path):
    return _make_pdf(tmp_path, "blank.pdf")


def _vision(screen, detail=None):
    calls = []

    def call(images, phase):
        calls.append((phase, tuple((image.page_idx, image.region) for image in images)))
        source = screen if phase == "page_screen" else detail
        if source is None:
            raise AssertionError("unexpected detail pass")
        return [source[(image.page_idx, image.region)] for image in images]

    call.calls = calls
    return call


def _mapping(votes):
    return {(vote.page_idx, vote.region): vote for vote in votes}


def _detail_votes(language, style, confidence=0.99, *, page_idx=1, overrides=None):
    # Match the page and overlapping text-strip regions in the classifier contract.
    images = [
        ClassificationImage(page_idx, region, b"png")
        for region in ("page", "top", "middle", "bottom")
    ]
    values = []
    for image in images:
        style_value, vote_confidence, primary = (overrides or {}).get(
            image.region, (style, confidence, True)
        )
        values.append(PageStyleVote(
            page_idx=image.page_idx,
            language=language,
            writing_style=style_value,
            confidence=vote_confidence,
            region=image.region,
            primary_content=primary,
        ))
    return _mapping(values)


def test_english_text_layer_skips_the_local_vision_model(english_only_pdf):
    def should_not_call(_images, _phase):
        raise AssertionError("English-only text should not call the local model")

    decision = classify_document(english_only_pdf, vision_call=should_not_call)
    assert decision.route is DocumentRoute.ENGLISH
    assert decision.language == "english"
    assert decision.text_direction == "ltr"
    assert decision.classifier_model == "text_layer"
    assert decision.confidence == 1.0


def test_text_backed_english_book_skips_vision_for_image_only_pages(tmp_path):
    path = tmp_path / "english-book-with-figures.pdf"
    figure = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 32, 32), False)
    figure.clear_with(0)
    figure_bytes = figure.tobytes("png")
    document = fitz.open()
    for _ in range(3):
        page = document.new_page()
        page.insert_text(
            (32, 60),
            "This is a full English book page with enough selectable body text. " * 3,
        )
    for _ in range(30):
        page = document.new_page()
        page.insert_image(fitz.Rect(40, 40, 560, 740), stream=figure_bytes)
    document.save(path)
    document.close()

    def should_not_call(_images, _phase):
        raise AssertionError("text-backed English documents must not call vision")

    decision = classify_document(path, vision_call=should_not_call)

    assert decision.route is DocumentRoute.ENGLISH
    assert decision.language == "english"
    assert decision.text_direction == "ltr"
    assert decision.confidence == 1.0
    assert decision.classifier_model == "text_layer"


def test_digital_arabic_text_layer_skips_local_vision(scanned_arabic_pdf, monkeypatch):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((
            TextPageEvidence(1, 120, 0),
        )),
    )

    def should_not_call(_images, _phase):
        raise AssertionError("digital Arabic text should not call the local model")

    decision = classify_document(scanned_arabic_pdf, vision_call=should_not_call)

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "arabic"
    assert decision.writing_style == "printed"
    assert decision.text_direction == "rtl"
    assert decision.confidence == 1.0
    assert decision.classifier_model == "text_layer"


def test_digital_arabic_and_clear_english_pages_are_mixed_without_vision(
    two_page_scan, monkeypatch
):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((
            TextPageEvidence(1, 0, 120),
            TextPageEvidence(2, 120, 0),
        )),
    )

    def should_not_call(_images, _phase):
        raise AssertionError("text-backed pages should not call the local model")

    decision = classify_document(two_page_scan, vision_call=should_not_call)

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "mixed"
    assert decision.writing_style == "printed"
    assert decision.text_direction == "rtl"
    assert decision.confidence == 1.0
    assert decision.classifier_model == "text_layer"


def test_one_substantive_arabic_page_overrides_a_300_page_english_book(
    english_only_pdf, monkeypatch
):
    pages = [
        TextPageEvidence(page_idx=index, arabic_chars=0, latin_chars=120)
        for index in range(1, 301)
    ]
    pages[149] = TextPageEvidence(page_idx=150, arabic_chars=120, latin_chars=0)
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence(tuple(pages)),
    )

    def should_not_call(_images, _phase):
        raise AssertionError("substantive Arabic text must bypass vision")

    decision = classify_document(english_only_pdf, vision_call=should_not_call)

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "mixed"
    assert decision.writing_style == "printed"
    assert decision.text_direction == "rtl"
    assert decision.confidence == 1.0
    assert decision.classifier_model == "text_layer"


def test_substantive_arabic_text_skips_vision_for_textless_pages(two_page_scan, monkeypatch):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((
            TextPageEvidence(1, 120, 0),
            TextPageEvidence(2, 0, 0),
        )),
    )
    calls = []

    def should_not_call(_images, _phase):
        calls.append("called")
        raise AssertionError("a real Arabic text layer is printed evidence")

    decision = classify_document(two_page_scan, vision_call=should_not_call)

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "arabic"
    assert decision.writing_style == "printed"
    assert calls == []


def test_clear_english_text_layer_ignores_embedded_image_language_vote(
    english_text_with_embedded_image_pdf,
):
    calls = []

    def screen_images(images, phase):
        calls.append((phase, [image.page_idx for image in images]))
        return [
            PageStyleVote(
                page_idx=image.page_idx,
                language="arabic",
                writing_style="printed",
                confidence=0.99,
                region=image.region,
                primary_content=True,
            )
            for image in images
        ]

    decision = classify_document(english_text_with_embedded_image_pdf, vision_call=screen_images)

    assert calls == []
    assert decision.route is DocumentRoute.ENGLISH
    assert decision.language == "english"
    assert decision.text_direction == "ltr"


def test_half_text_backed_english_book_skips_vision_for_scanned_page(
    english_cover_with_scanned_page_pdf,
):
    calls = []

    def classify_sparse_page(images, phase):
        calls.append((phase, [(image.page_idx, image.region) for image in images]))
        return [
            PageStyleVote(
                page_idx=image.page_idx,
                language="arabic",
                writing_style="printed",
                confidence=0.99,
                region=image.region,
                primary_content=True,
            )
            for image in images
        ]

    decision = classify_document(
        english_cover_with_scanned_page_pdf,
        vision_call=classify_sparse_page,
    )

    assert decision.route is DocumentRoute.ENGLISH
    assert decision.language == "english"
    assert decision.text_direction == "ltr"
    assert calls == []


def test_mixed_body_text_uses_arabic_printed_route(mixed_text_pdf, monkeypatch):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((TextPageEvidence(1, 45, 250),)),
    )
    screen = _mapping([
        PageStyleVote(1, "mixed", "printed", 0.99, region="page")
    ])
    vision = _vision(
        screen,
        _detail_votes("mixed", "printed"),
    )
    decision = classify_document(mixed_text_pdf, vision_call=vision)
    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "mixed"
    assert decision.text_direction == "rtl"


def test_scanned_printed_arabic_is_not_blocked_as_handwritten(scanned_arabic_pdf):
    vision = _vision(_mapping([
        PageStyleVote(1, "arabic", "printed", 0.94, region="page")
    ]), _detail_votes("arabic", "printed"))
    decision = classify_document(scanned_arabic_pdf, vision_call=vision)
    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.writing_style == "printed"
    assert [phase for phase, _ in vision.calls] == ["scan"]
    assert vision.calls[0][1] == ((1, "page"),)


def test_english_paper_with_one_arabic_citation_stays_english_without_vision(
    english_only_pdf, monkeypatch
):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((TextPageEvidence(1, 25, 400),)),
    )

    def should_not_call(_images, _phase):
        raise AssertionError("an isolated Arabic citation is not body text")

    decision = classify_document(english_only_pdf, vision_call=should_not_call)

    assert decision.route is DocumentRoute.ENGLISH
    assert decision.language == "english"


def test_half_text_backed_english_document_does_not_vision_classify_sparse_page(
    two_page_scan, monkeypatch
):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((
            TextPageEvidence(1, 0, 0),
            TextPageEvidence(2, 0, 300),
        )),
    )
    vision = _vision(
        _mapping([PageStyleVote(1, "arabic", "printed", 0.99, region="page")]),
        _detail_votes("arabic", "printed", page_idx=1),
    )

    decision = classify_document(two_page_scan, vision_call=vision)

    assert decision.route is DocumentRoute.ENGLISH
    assert decision.language == "english"
    assert vision.calls == []


def test_blank_cover_unknown_vote_does_not_force_printed_body_to_uncertain(
    two_page_scan, monkeypatch
):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((
            TextPageEvidence(1, 0, 0),
            TextPageEvidence(2, 80, 0),
        )),
    )
    screen = _mapping([
        PageStyleVote(1, "unknown", "unknown", 0.2, region="page", primary_content=False),
        PageStyleVote(2, "arabic", "printed", 0.99, region="page", primary_content=True),
    ])
    detail = {}
    detail.update(_detail_votes(
        "unknown", "unknown", confidence=0.2, page_idx=1,
        overrides={region: ("unknown", 0.2, False) for region in ("page", "top", "middle", "bottom")},
    ))
    detail.update(_detail_votes("arabic", "printed", page_idx=2))

    decision = classify_document(two_page_scan, vision_call=_vision(screen, detail))

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "arabic"


def test_nonprimary_arabic_cover_does_not_decide_document_style(
    two_page_scan, monkeypatch
):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((
            TextPageEvidence(1, 35, 0),
            TextPageEvidence(2, 80, 0),
        )),
    )
    screen = _mapping([
        PageStyleVote(1, "arabic", "unknown", 0.5, region="page", primary_content=False),
        PageStyleVote(2, "arabic", "printed", 0.99, region="page", primary_content=True),
    ])
    detail = {}
    detail.update(_detail_votes(
        "arabic", "unknown", confidence=0.5, page_idx=1,
        overrides={region: ("unknown", 0.5, False) for region in ("page", "top", "middle", "bottom")},
    ))
    detail.update(_detail_votes("arabic", "printed", page_idx=2))

    decision = classify_document(two_page_scan, vision_call=_vision(screen, detail))

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.writing_style == "printed"


def test_handwriting_like_print_with_conflicting_votes_abstains(decorative_printed_pdf):
    screen = _mapping([
        PageStyleVote(1, "arabic", "mixed", 0.76, region="page")
    ])
    detail = _detail_votes(
        "arabic", "printed", overrides={"bottom": ("handwritten", 0.91, True)}
    )
    decision = arabic_classifier.aggregate_votes(
        DocumentTextEvidence((TextPageEvidence(1, 0, 0),)),
        list(screen.values()),
        list(detail.values()),
    )
    assert decision.route is DocumentRoute.ARABIC_STYLE_UNCERTAIN


def test_confident_handwriting_needs_page_and_region_consensus(confident_handwriting_pdf):
    screen = _mapping([
        PageStyleVote(1, "arabic", "handwritten", 0.99, region="page")
    ])
    detail = _detail_votes("arabic", "handwritten")
    decision = arabic_classifier.aggregate_votes(
        DocumentTextEvidence((TextPageEvidence(1, 0, 0),)),
        list(screen.values()),
        list(detail.values()),
    )
    assert decision.route is DocumentRoute.ARABIC_HANDWRITTEN


def test_one_page_handwriting_with_a_split_region_vote_abstains(one_page_scan):
    screen = _mapping([
        PageStyleVote(1, "arabic", "handwritten", 0.99, region="page")
    ])
    detail = _detail_votes(
        "arabic",
        "handwritten",
        overrides={"bottom": ("printed", 0.99, True)},
    )
    decision = arabic_classifier.aggregate_votes(
        DocumentTextEvidence((TextPageEvidence(1, 0, 0),)),
        list(screen.values()),
        list(detail.values()),
    )
    assert decision.route is DocumentRoute.ARABIC_STYLE_UNCERTAIN


def test_isolated_handwritten_signature_does_not_change_printed_body_style(
    printed_form_with_signature_pdf,
):
    screen = _mapping([
        PageStyleVote(1, "arabic", "printed", 0.85, region="page")
    ])
    detail = _detail_votes(
        "arabic",
        "printed",
        overrides={"bottom": ("handwritten", 0.99, False)},
    )
    decision = arabic_classifier.aggregate_votes(
        DocumentTextEvidence((TextPageEvidence(1, 0, 0),)),
        list(screen.values()),
        list(detail.values()),
    )
    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.writing_style == "printed"


def test_unrecognized_scan_response_fails_closed_instead_of_defaulting_to_english(no_readable_text_pdf):
    screen = _mapping([
        PageStyleVote(1, "unknown", "unknown", 0.2, region="page")
    ])
    detail = _detail_votes("unknown", "unknown", confidence=0.2)
    with pytest.raises(ArabicClassifierResponseInvalid):
        classify_document(no_readable_text_pdf, vision_call=_vision(screen, detail))


def test_no_primary_votes_abstains_with_zero_confidence(no_readable_text_pdf):
    screen = _mapping([
        PageStyleVote(
            1, "unknown", "unknown", 0.2,
            region="page", primary_content=False,
        )
    ])
    detail = _detail_votes(
        "unknown",
        "unknown",
        confidence=0.2,
        overrides={
            "page": ("unknown", 0.2, False),
            "top": ("unknown", 0.2, False),
            "middle": ("unknown", 0.2, False),
            "bottom": ("unknown", 0.2, False),
        },
    )

    decision = arabic_classifier.aggregate_votes(
        DocumentTextEvidence((TextPageEvidence(1, 0, 0),)),
        list(screen.values()),
        list(detail.values()),
    )

    assert decision.route is DocumentRoute.ARABIC_STYLE_UNCERTAIN
    assert decision.language == "unknown"
    assert decision.confidence == 0.0


def test_english_and_arabic_evidence_on_different_pages_is_mixed():
    evidence = DocumentTextEvidence((
        TextPageEvidence(page_idx=1, arabic_chars=0, latin_chars=120),
        TextPageEvidence(page_idx=2, arabic_chars=45, latin_chars=0),
    ))
    assert evidence.has_arabic_body
    assert evidence.has_latin_body
    assert evidence.language == "mixed"


def test_arabic_count_ignores_diacritics_and_punctuation():
    assert arabic_classifier.count_arabic_letters("، مُرَحَّبًا ١٢") == 5


def test_script_counts_ignore_urls_and_isolated_latin_formula_symbols():
    assert arabic_classifier.count_latin_letters(
        "https://example.com x + y; Q = 1"
    ) == 0
    assert arabic_classifier.count_latin_letters("The printed body has Latin words.") > 20


def test_all_pages_are_screened_and_suspicious_details_are_individual(two_page_scan):
    screen = _mapping([
        PageStyleVote(1, "arabic", "printed", 0.70, region="page"),
        PageStyleVote(2, "arabic", "handwritten", 0.99, region="page"),
    ])
    detail = {}
    detail.update(_detail_votes("arabic", "printed", page_idx=1))
    detail.update(_detail_votes("arabic", "handwritten", page_idx=2))
    vision = _vision(screen, detail)

    page_images = [
        ClassificationImage(page_idx, "page", b"png") for page_idx in (1, 2)
    ]
    screen_votes = arabic_classifier.call_in_batches(
        page_images, 2, vision, "page_screen"
    )
    detail_votes = []
    for page_idx in arabic_classifier._suspicious_page_indices(screen_votes):
        page_details = [
            ClassificationImage(page_idx, region, b"png")
            for region in ("page", "top", "middle", "bottom")
        ]
        detail_votes.extend(
            arabic_classifier.call_in_batches(page_details, 1, vision, "detail")
        )
    decision = arabic_classifier.aggregate_votes(
        DocumentTextEvidence((TextPageEvidence(1, 0, 0), TextPageEvidence(2, 0, 0))),
        screen_votes,
        detail_votes,
    )

    assert decision.route is DocumentRoute.ARABIC_STYLE_UNCERTAIN
    screen_calls = [regions for phase, regions in vision.calls if phase == "page_screen"]
    detail_calls = [regions for phase, regions in vision.calls if phase == "detail"]
    assert [page for call in screen_calls for page, _ in call] == [1, 2]
    assert len(detail_calls) == 2
    assert all(len({page for page, _ in call}) == 1 for call in detail_calls)


def test_detail_rerender_is_higher_resolution_and_keeps_region_views(scanned_arabic_pdf):
    screen = arabic_classifier.render_page_images(scanned_arabic_pdf, [1])
    detail = arabic_classifier.render_page_images(
        scanned_arabic_pdf, [1], include_regions=True
    )
    screen_page = fitz.open(stream=screen[0].image_bytes, filetype="png")[0].rect
    detail_page = fitz.open(stream=detail[0].image_bytes, filetype="png")[0].rect
    assert detail_page.width > screen_page.width
    assert [image.region for image in detail] == ["page", "top", "middle", "bottom"]


def test_local_router_sends_strict_json_request_without_cloud_credentials(monkeypatch):
    captured = {}
    settings = arabic_classifier.settings.model_dump()
    settings["arabic_router_timeout_seconds"] = 123.5
    monkeypatch.setattr(
        arabic_classifier,
        "settings",
        SimpleNamespace(**settings),
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": json.dumps({"votes": [
                {"page_idx": 1, "region": "page", "language": "arabic",
                 "writing_style": "printed", "confidence": 0.97,
                 "primary_content": True, "evidence": "printed body"}
            ]})}}

    def fake_post(url, *, json, timeout, trust_env):
        captured.update(url=url, body=json, timeout=timeout, trust_env=trust_env)
        return Response()

    monkeypatch.setattr(arabic_classifier.httpx, "post", fake_post)
    votes = call_local_router([ClassificationImage(1, "page", b"png-bytes")], "page_screen")
    assert votes == [PageStyleVote(
        page_idx=1, language="arabic", writing_style="printed", confidence=0.97,
        evidence="printed body", region="page", primary_content=True,
    )]
    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["model"] == "qwen3-vl:4b-instruct"
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["temperature"] == 0
    assert captured["body"]["options"]["num_ctx"] == 3584
    assert isinstance(captured["body"]["format"], dict)
    assert captured["body"]["messages"][0]["images"]
    assert captured["timeout"] > 0
    assert captured["timeout"] == 123.5
    assert captured["trust_env"] is False
    assert "api_key" not in captured["body"]


def test_local_router_context_size_is_capped_for_large_batches(monkeypatch):
    images = [
        ClassificationImage(index, "page", b"png")
        for index in range(1, 30)
    ]
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": json.dumps({"votes": [
                {
                    "page_idx": image.page_idx,
                    "region": image.region,
                    "language": "arabic",
                    "writing_style": "printed",
                    "confidence": 0.97,
                    "primary_content": True,
                    "evidence": "printed body",
                }
                for image in images
            ]})}}

    def fake_post(_url, *, json, **_kwargs):
        captured.update(body=json)
        return Response()

    monkeypatch.setattr(arabic_classifier.httpx, "post", fake_post)

    call_local_router(images, "page_screen")

    assert captured["body"]["options"]["num_ctx"] == 32768


def test_local_router_logs_only_truncated_ollama_400_body(monkeypatch, caplog):
    error_body = "context length exceeded: " + ("x" * 400)
    response = httpx.Response(
        400,
        text=error_body,
        request=httpx.Request("POST", "http://localhost:11434/api/chat"),
    )
    monkeypatch.setattr(
        arabic_classifier.httpx,
        "post",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(arabic_classifier.ArabicClassifierUnavailable):
        call_local_router([ClassificationImage(1, "page", b"private-image")], "page_screen")

    assert caplog.records[-1].getMessage() == (
        "Local Ollama Arabic classifier returned HTTP 400: " + error_body[:300]
    )
    assert error_body not in caplog.text
    assert "Classify the visible language" not in caplog.text
    assert "cHJpdmF0ZS1pbWFnZQ==" not in caplog.text


def test_local_router_refuses_a_public_cloud_endpoint(monkeypatch):
    monkeypatch.setattr(
        arabic_classifier.settings,
        "arabic_router_base_url",
        "https://ollama.com",
    )
    monkeypatch.setattr(
        arabic_classifier.httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("must not send document images to a public endpoint"),
    )
    with pytest.raises(arabic_classifier.ArabicClassifierUnavailable):
        call_local_router([ClassificationImage(1, "page", b"png")], "page_screen")


def test_scan_classifier_samples_three_evenly_spread_single_pages(tmp_path):
    pdf_path = _make_scanned_pdf(tmp_path, "forty-page-scan.pdf", 40)
    calls = []

    def classify_english(images, phase):
        assert phase == "scan"
        assert len(images) == 1
        calls.append((images[0].page_idx, len(images)))
        return [PageStyleVote(
            page_idx=images[0].page_idx,
            language="english",
            writing_style="unknown",
            confidence=1.0,
            region=images[0].region,
        )]

    decision = classify_document(pdf_path, vision_call=classify_english)

    assert [page_idx for page_idx, _ in calls] == [1, 20, 40]
    assert all(image_count == 1 for _, image_count in calls)
    assert decision.route is DocumentRoute.ENGLISH
    assert decision.classifier_model == arabic_classifier.settings.arabic_router_model
    assert decision.confidence == 1.0


def test_scan_arabic_vote_routes_printed_arabic_and_counts_agreement(tmp_path):
    pdf_path = _make_scanned_pdf(tmp_path, "arabic-scan.pdf", 3)
    languages = {1: "english", 2: "arabic", 3: "english"}

    def classify_pages(images, phase):
        assert phase == "scan"
        assert len(images) == 1
        image = images[0]
        return [PageStyleVote(
            page_idx=image.page_idx,
            language=languages[image.page_idx],
            writing_style="printed",
            confidence=1.0,
            region=image.region,
        )]

    decision = classify_document(pdf_path, vision_call=classify_pages)

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "mixed"
    assert decision.writing_style == "printed"
    assert decision.text_direction == "rtl"
    assert decision.confidence == pytest.approx(1 / 3)


def test_scan_handwritten_majority_uses_handwritten_route(tmp_path):
    pdf_path = _make_scanned_pdf(tmp_path, "handwritten-scan.pdf", 3)
    votes = {
        1: ("arabic", "handwritten"),
        2: ("mixed", "handwritten"),
        3: ("arabic", "printed"),
    }

    def classify_pages(images, phase):
        assert phase == "scan"
        assert len(images) == 1
        image = images[0]
        language, style = votes[image.page_idx]
        return [PageStyleVote(
            page_idx=image.page_idx,
            language=language,
            writing_style=style,
            confidence=1.0,
            region=image.region,
        )]

    decision = classify_document(pdf_path, vision_call=classify_pages)

    assert decision.route is DocumentRoute.ARABIC_HANDWRITTEN
    assert decision.language == "mixed"
    assert decision.writing_style == "handwritten"
    assert decision.confidence == pytest.approx(2 / 3)


def test_scan_skips_blank_samples_and_fails_when_all_sampled_pages_are_blank(tmp_path):
    pdf_path = _make_scanned_pdf(
        tmp_path, "scan-with-blank-middle.pdf", 3, blank_pages=(2,)
    )
    calls = []

    def classify_nonblank(images, phase):
        assert phase == "scan"
        assert len(images) == 1
        calls.append(images[0].page_idx)
        return [PageStyleVote(
            page_idx=images[0].page_idx,
            language="english",
            writing_style="unknown",
            confidence=1.0,
            region=images[0].region,
        )]

    decision = classify_document(pdf_path, vision_call=classify_nonblank)
    assert calls == [1, 3]
    assert decision.route is DocumentRoute.ENGLISH

    blank_pdf = _make_scanned_pdf(tmp_path, "all-blank.pdf", 3, blank_pages=(1, 2, 3))
    with pytest.raises(ArabicClassifierResponseInvalid):
        classify_document(
            blank_pdf,
            vision_call=lambda *_args: pytest.fail("blank pages must not be sent to vision"),
        )


def test_scan_with_only_unknown_votes_fails_closed(tmp_path):
    pdf_path = _make_scanned_pdf(tmp_path, "unknown-scan.pdf", 3)

    def classify_unknown(images, phase):
        assert phase == "scan"
        image = images[0]
        return [PageStyleVote(
            page_idx=image.page_idx,
            language="unknown",
            writing_style="unknown",
            confidence=0.0,
            region=image.region,
        )]

    with pytest.raises(ArabicClassifierResponseInvalid):
        classify_document(pdf_path, vision_call=classify_unknown)


def test_local_router_scan_uses_one_small_image_plain_prompt_and_tolerant_parse(monkeypatch):
    captured = {}
    local_settings = arabic_classifier.settings.model_dump()
    local_settings["arabic_router_timeout_seconds"] = 123.5
    monkeypatch.setattr(arabic_classifier, "settings", SimpleNamespace(**local_settings))

    document = fitz.open()
    page = document.new_page(width=612, height=792)
    large_png = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    document.close()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "LANGUAGE: Arabic.\nSTYLE: PRINTED!"}}

    def fake_post(url, *, json, timeout, trust_env):
        captured.update(url=url, body=json, timeout=timeout, trust_env=trust_env)
        return Response()

    monkeypatch.setattr(arabic_classifier.httpx, "post", fake_post)
    votes = call_local_router(
        [ClassificationImage(7, "page", large_png)], "scan"
    )

    assert votes == [PageStyleVote(
        page_idx=7,
        language="arabic",
        writing_style="printed",
        confidence=1.0,
        region="page",
        primary_content=True,
    )]
    body = captured["body"]
    assert "format" not in body
    assert len(body["messages"][0]["images"]) == 1
    sent_png = base64.b64decode(body["messages"][0]["images"][0])
    sent_pixmap = fitz.Pixmap(sent_png)
    assert max(sent_pixmap.width, sent_pixmap.height) <= 1024
    assert body["options"]["temperature"] == 0
    assert body["options"]["num_predict"] <= 24
    assert body["options"]["num_ctx"] >= 2048
    assert captured["timeout"] == 123.5
    assert captured["trust_env"] is False


def test_local_router_scan_rejects_unparseable_plain_response(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "The image shows a page of printed Arabic."}}

    monkeypatch.setattr(arabic_classifier.httpx, "post", lambda *_args, **_kwargs: Response())

    document = fitz.open()
    image_bytes = document.new_page().get_pixmap().tobytes("png")
    document.close()

    with pytest.raises(ArabicClassifierResponseInvalid):
        call_local_router([ClassificationImage(1, "page", image_bytes)], "scan")


@pytest.mark.parametrize("bad_content", [
    "not json",
    '{"votes":[{"page_idx":1,"region":"page","language":"arabic",'
    '"writing_style":"script","confidence":0.9,"primary_content":true,"evidence":""}]}',
    '{"votes":[{"page_idx":1,"region":"page","language":"arabic",'
    '"writing_style":"printed","confidence":"0.9","primary_content":true,"evidence":""}]}',
    '{"votes":[{"page_idx":1,"region":"page","language":[], '
    '"writing_style":"printed","confidence":0.9,"primary_content":true,"evidence":""}]}',
    '{"votes":[]}',
])
def test_local_router_rejects_malformed_or_incomplete_votes(monkeypatch, bad_content):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": bad_content}}

    monkeypatch.setattr(arabic_classifier.httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError):
        call_local_router([ClassificationImage(1, "page", b"png")], "page_screen")
