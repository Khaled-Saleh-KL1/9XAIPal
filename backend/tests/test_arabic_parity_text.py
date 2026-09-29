import pytest

from app.chat.grounding import split_claims
from app.chat.paper_agent import cited_sequences
from app.extraction.chunker import _split_text_into_paragraphs


def test_long_english_paragraph_keeps_its_existing_sentence_boundaries():
    sentence = "A" * 169 + "."
    paragraph = " ".join([sentence] * 5)

    assert _split_text_into_paragraphs(paragraph) == [
        " ".join([sentence] * 3),
        " ".join([sentence] * 2),
    ]


@pytest.mark.parametrize("punctuation", ["؟", "۔"])
def test_long_arabic_paragraph_splits_after_arabic_sentence_punctuation(punctuation):
    endings = [punctuation, ".", ".", ".", punctuation]
    sentences = ["ت" * 169 + end for end in endings]
    paragraph = " ".join(sentences)

    assert len(paragraph) > 850
    assert _split_text_into_paragraphs(paragraph) == [
        " ".join(sentences[:3]),
        " ".join(sentences[3:]),
    ]


def test_grounding_keeps_english_sentence_splitting_unchanged():
    answer = "The first statement carries enough detail. The second statement does too."

    assert [claim.text for claim in split_claims(answer)] == [
        "The first statement carries enough detail.",
        "The second statement does too.",
    ]


@pytest.mark.parametrize("punctuation", ["؟", "۔"])
def test_grounding_splits_arabic_claims_at_arabic_sentence_punctuation(punctuation):
    first = "توضح هذه الجملة العربية معلومة مهمة"
    second = "يبين هذا الادعاء العربي نتيجة مختلفة بوضوح"

    assert [claim.text for claim in split_claims(f"{first}{punctuation} {second}")] == [
        f"{first}{punctuation}",
        second,
    ]


def test_paper_agent_extracts_ascii_and_arabic_indic_citations():
    assert cited_sequences("The cited blocks are [[42]] and [[7, 42]].") == [42, 7]
    assert cited_sequences("المقاطع المشار إليها [[٤٢]] و[[۷]] مهمة.") == [42, 7]


@pytest.mark.parametrize("digits", ["٤٢", "۴۲"])
def test_grounding_parses_and_strips_arabic_indic_citations(digits):
    english = split_claims("The paper supports the result with evidence [[42]].")
    arabic = split_claims(f"تدعم هذه الدراسة النتيجة بالأدلة الواضحة [[{digits}]].")

    assert english[0].refs == [(None, 42)]
    assert english[0].text == "The paper supports the result with evidence."
    assert arabic[0].refs == [(None, 42)]
    assert "[[" not in arabic[0].text
