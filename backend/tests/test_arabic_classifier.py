import json

import fitz
import pytest

from app.extraction.arabic_types import (
    DocumentRoute,
    PageStyleVote,
)
from app.extraction import arabic_classifier
from app.extraction.arabic_classifier import (
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
    doc.save(path)
    doc.close()
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


def test_english_cover_does_not_hide_an_arabic_scanned_page(
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

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "mixed"
    assert decision.text_direction == "rtl"
    assert calls[0] == ("page_screen", [(2, "page")])
    assert calls[1] == (
        "detail",
        [(2, "page"), (2, "top"), (2, "middle"), (2, "bottom")],
    )


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
    assert [phase for phase, _ in vision.calls] == ["page_screen", "detail"]
    assert vision.calls[1][1] == (
        (1, "page"), (1, "top"), (1, "middle"), (1, "bottom")
    )


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


def test_arabic_body_with_english_abstract_uses_mixed_arabic_route(
    two_page_scan, monkeypatch
):
    monkeypatch.setattr(
        arabic_classifier,
        "inspect_text_layers",
        lambda _path: DocumentTextEvidence((
            TextPageEvidence(1, 80, 0),
            TextPageEvidence(2, 0, 300),
        )),
    )
    vision = _vision(
        _mapping([PageStyleVote(1, "arabic", "printed", 0.99, region="page")]),
        _detail_votes("arabic", "printed", page_idx=1),
    )

    decision = classify_document(two_page_scan, vision_call=vision)

    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.language == "mixed"
    assert [page for phase, images in vision.calls for page, _ in images if phase == "page_screen"] == [1]


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
    decision = classify_document(
        decorative_printed_pdf,
        vision_call=_vision(screen, detail),
    )
    assert decision.route is DocumentRoute.ARABIC_STYLE_UNCERTAIN


def test_confident_handwriting_needs_page_and_region_consensus(confident_handwriting_pdf):
    screen = _mapping([
        PageStyleVote(1, "arabic", "handwritten", 0.99, region="page")
    ])
    detail = _detail_votes("arabic", "handwritten")
    decision = classify_document(
        confident_handwriting_pdf,
        vision_call=_vision(screen, detail),
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
    decision = classify_document(one_page_scan, vision_call=_vision(screen, detail))
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
    decision = classify_document(
        printed_form_with_signature_pdf,
        vision_call=_vision(screen, detail),
    )
    assert decision.route is DocumentRoute.ARABIC_PRINTED
    assert decision.writing_style == "printed"


def test_no_readable_text_abstains_instead_of_defaulting_to_english(no_readable_text_pdf):
    screen = _mapping([
        PageStyleVote(1, "unknown", "unknown", 0.2, region="page")
    ])
    detail = _detail_votes("unknown", "unknown", confidence=0.2)
    decision = classify_document(no_readable_text_pdf, vision_call=_vision(screen, detail))
    assert decision.route is DocumentRoute.ARABIC_STYLE_UNCERTAIN
    assert decision.language == "unknown"
    assert decision.text_direction == "auto"


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

    decision = classify_document(
        no_readable_text_pdf,
        vision_call=_vision(screen, detail),
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

    decision = classify_document(two_page_scan, vision_call=vision)

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
    assert isinstance(captured["body"]["format"], dict)
    assert captured["body"]["messages"][0]["images"]
    assert captured["timeout"] > 0
    assert captured["trust_env"] is False
    assert "api_key" not in captured["body"]


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
