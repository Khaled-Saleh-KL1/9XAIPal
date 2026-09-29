from app.extraction.heading_repair import (
    heading_level,
    is_not_a_heading,
    normalize_title,
    repair_headings,
)
from app.extraction.pipeline_sync import infer_pdf_title


def _heading(text, level=2):
    return {
        "chunk_type": "heading",
        "markdown": f"{'#' * level} {text}",
        "plain_text": text,
        "page_start": 1,
        "heading_path": None,
    }


def test_english_heading_normalization_keeps_its_existing_output():
    assert normalize_title("3.2.1 Scaled Dot-Product Attention") == "scaled dot product attention"
    assert normalize_title("Part 1: Amazon Bedrock Foundations") == "amazon bedrock foundations"


def test_arabic_heading_normalization_retains_arabic_words_and_numbers():
    assert normalize_title("٣. مقدمة الذكاء الاصطناعي") == "مقدمة الذكاء الاصطناعي"
    assert normalize_title("الفصل ٢: التعلم العميق") == "الفصل ٢ التعلم العميق"


def test_english_chapter_and_caption_repair_decisions_are_unchanged():
    assert is_not_a_heading("FIGURE 2.1. System overview") == "caption"
    assert is_not_a_heading("A meaningful English chapter title") is None
    chunks = [_heading("Chapter 3: Memory")]

    repair_headings(chunks)

    assert heading_level(chunks[0]) == 1


def test_arabic_chapters_are_promoted_and_captions_are_demoted():
    chapter = _heading("الفصل الثالث: التعلم العميق")
    caption = _heading("الشكل ١: مخطط النظام")

    report = repair_headings([chapter, caption])

    assert heading_level(chapter) == 1
    assert chapter["heading_path"] == ["الفصل الثالث: التعلم العميق"]
    assert caption["chunk_type"] == "text"
    assert report.promoted_chapters == 1
    assert report.reasons == {"caption": 1}


def test_english_title_inference_stays_the_same_and_arabic_sections_are_skipped():
    assert infer_pdf_title([_heading("Introduction", level=1)], "1706.03762.pdf") is None
    assert infer_pdf_title([_heading("Attention Is All You Need", level=1)], "1706.03762.pdf") == "Attention Is All You Need"
    assert infer_pdf_title([_heading("المقدمة", level=1)], "1706.03762.pdf") is None
    assert infer_pdf_title([_heading("الخلاصة", level=1)], "1706.03762.pdf") is None
    assert infer_pdf_title([_heading("الذكاء الاصطناعي في البحث", level=1)], "1706.03762.pdf") == "الذكاء الاصطناعي في البحث"
