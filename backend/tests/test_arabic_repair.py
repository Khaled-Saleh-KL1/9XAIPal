import unicodedata

import pytest

from app.extraction.arabic_repair import repair_gemma_page
from app.extraction.arabic_types import ArabicOcrPage, GemmaOutputInvalid


def ocr_page(
    number: int,
    text: str,
    *,
    provider: str = "gemma4_arabic_fallback",
) -> ArabicOcrPage:
    return ArabicOcrPage(
        page_number=number,
        raw_markdown=text,
        markdown=text,
        provider=provider,
        model="test-model",
    )


def test_gemini_page_is_returned_byte_for_byte():
    original = ocr_page(1, "إِنَّ النص\u200f", provider="gemini_arabic_flash")

    assert repair_gemma_page(original, source_text="") is original


def test_gemma_cleanup_is_canonical_not_orthographic():
    original = ocr_page(1, "ﺍﻟﻨﺺ\x00")

    repaired = repair_gemma_page(original, source_text="")

    assert repaired.markdown == "النص"
    assert repaired.raw_markdown == "ﺍﻟﻨﺺ\x00"
    assert repaired.repaired is True


@pytest.mark.parametrize(
    "text",
    [
        "أإآا",
        "ةه",
        "يى",
        "نَصٌّ",
    ],
)
def test_repair_does_not_normalize_arabic_orthography_or_remove_diacritics(text):
    original = ocr_page(1, text)

    repaired = repair_gemma_page(original, source_text="")

    if text == "نَصٌّ":
        original_marks = sorted(
            character
            for character in text
            if unicodedata.category(character).startswith("M")
        )
        repaired_marks = sorted(
            character
            for character in repaired.markdown
            if unicodedata.category(character).startswith("M")
        )
        assert repaired_marks == original_marks
    else:
        assert repaired.markdown == text
        assert repaired.repaired is False


def test_duplicate_zero_width_marks_are_collapsed_but_single_mark_is_preserved():
    original = ocr_page(1, "ا\u200d\u200dل")

    repaired = repair_gemma_page(original, source_text="")

    assert repaired.markdown == "ا\u200dل"
    assert repaired.repaired is True


def test_usable_source_text_repairs_only_a_uniquely_anchored_span():
    source = "هذا نص عربي طويل يثبت أن السياق المحيط بهذه الكلمة واضح للغاية في الصفحة"
    original = ocr_page(
        1,
        "هذا نص عربي طويل يثبت أن السياق المحيط بهذه الكلمة واضح خ للغاية في الصفحة",
    )

    repaired = repair_gemma_page(original, source_text=source)

    assert repaired.markdown == source
    assert repaired.raw_markdown == original.raw_markdown
    assert repaired.repaired is True


def test_source_text_can_recover_a_missing_english_ff_in_mixed_arabic():
    source = "هذا نص عربي طويل يشرح the difference بين الكلمات بطريقة واضحة للقارئ"
    original = ocr_page(
        1,
        "هذا نص عربي طويل يشرح the diference بين الكلمات بطريقة واضحة للقارئ",
    )

    repaired = repair_gemma_page(original, source_text=source)

    assert repaired.markdown == source
    assert repaired.repaired is True


def test_ambiguous_source_alignment_leaves_ocr_unchanged():
    original = ocr_page(1, "هذا نص قصير اختلف")

    repaired = repair_gemma_page(original, source_text="هذا نص قصير مختلف")

    assert repaired.markdown == original.markdown
    assert repaired.repaired is False


def test_repetition_loop_is_rejected_not_deleted():
    sentence = "هذه جملة مكررة بشكل طويل وواضح لا يجوز حذفها بصمت"
    original = ocr_page(1, "\n\n".join([sentence] * 3))

    with pytest.raises(GemmaOutputInvalid):
        repair_gemma_page(original, source_text="")
