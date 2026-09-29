import pytest

from app.services import reference_finder as rf, references as refs
from app.services.references import parse_references, title_candidates


def _chunks_for_heading(heading):
    return [
        {"chunk_type": "heading", "plain_text": heading},
        {
            "chunk_type": "text",
            "plain_text": "- [1] A. Author. First paper title. Journal, 2020.\n"
            "- [2] B. Author. Second paper title. Journal, 2021.",
        },
        {"chunk_type": "heading", "plain_text": "Appendix"},
    ]


def test_english_references_heading_and_entries_keep_their_existing_output():
    assert parse_references(_chunks_for_heading("References")) == [
        {"number": 1, "raw_text": "A. Author. First paper title. Journal, 2020."},
        {"number": 2, "raw_text": "B. Author. Second paper title. Journal, 2021."},
    ]
    assert parse_references(_chunks_for_heading("7. References")) == []


@pytest.mark.parametrize(
    "heading",
    ["المراجع", "المصادر", "قائمة المراجع", "المراجع والمصادر", "مراجع البحث"],
)
def test_arabic_bibliography_headings_yield_the_same_entries(heading):
    assert parse_references(_chunks_for_heading(heading)) == [
        {"number": 1, "raw_text": "A. Author. First paper title. Journal, 2020."},
        {"number": 2, "raw_text": "B. Author. Second paper title. Journal, 2021."},
    ]


def test_reference_title_similarity_keeps_english_and_matches_arabic_words():
    english = "Attention Is All You Need"
    arabic = "الانتباه متعدد الوسائط"

    assert rf.title_similarity(english, english) == 1.0
    assert rf.title_similarity(arabic, arabic) == 1.0
    assert rf.title_similarity(arabic, "التعلم العميق") == 0.0


def test_reference_year_cleanup_keeps_ascii_and_strips_both_arabic_digit_sets():
    assert refs._TRAILING_YEAR_RE.sub("", "Attention is all you need, 2020.") == "Attention is all you need"
    for year in ("٢٠٢٠", "۲۰۲۰"):
        assert year not in refs._TRAILING_YEAR_RE.sub("", f"عنوان تجريبي، {year}.")


def test_arabic_question_mark_ends_a_reference_title_segment():
    raw = "أحمد علي. هل تنجح هذه الطريقة؟ مجلة العلوم، ٢٠٢٠."

    assert title_candidates(raw, limit=1) == ["هل تنجح هذه الطريقة؟"]
