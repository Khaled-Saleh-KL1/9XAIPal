import json

import fitz

from app.core.config import settings
from app.extraction import arabic_ocr
from app.extraction.arabic_types import (
    ArabicOcrPage,
    ClassificationDecision,
    DocumentRoute,
    OcrUsage,
)


def test_arabic_ocr_artifacts_join_combining_marks_without_merging_words(tmp_path):
    source_pdf = tmp_path / "source.pdf"
    pdf = fitz.open()
    pdf.new_page()
    pdf.save(source_pdf)
    pdf.close()

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    corrupted_heading = "ف \u064F \u062A \u064F \u0648 \u064E\u0651 \u0629 العطوف"
    arabic_page = ArabicOcrPage(
        page_number=1,
        raw_markdown=f"# {corrupted_heading}\n\nالمعنىُ هنا واضح.",
        markdown=f"# {corrupted_heading}\n\nالمعنىُ هنا واضح.",
        provider="gemini_arabic_flash",
        model="test-model",
    )
    classification = ClassificationDecision(
        route=DocumentRoute.ARABIC_PRINTED,
        language="arabic",
        writing_style="printed",
        text_direction="rtl",
        confidence=1.0,
        classifier_model="test-classifier",
    )

    arabic_ocr._write_artifacts(
        staging_dir,
        [arabic_page],
        "gemini_arabic_flash",
        classification,
        [{"provider": "gemini_arabic_flash", "pages": [1]}],
        [],
        OcrUsage(),
        1,
        settings,
        source_pdf_path=source_pdf,
    )

    markdown = (staging_dir / "document.md").read_text(encoding="utf-8")
    content_list = json.loads(
        (staging_dir / "content_list.json").read_text(encoding="utf-8")
    )
    assert "# فُتُوَّة العطوف" in markdown
    assert "المعنىُ هنا واضح." in markdown
    assert content_list[0]["text"] == "فُتُوَّة العطوف"
    assert content_list[1]["text"] == "المعنىُ هنا واضح."
