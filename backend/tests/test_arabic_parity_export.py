from app.api.v1.endpoints.export import _attachment
from app.services.export import _bibtex_key, _slug, markdown_filename, to_bibtex, to_library_csv


def test_english_export_names_and_keys_keep_their_existing_output():
    assert _slug("Attention Is All You Need") == "attention-is-all-you-need"
    assert markdown_filename({"title": "Attention Is All You Need"}) == "attention-is-all-you-need.md"
    assert _bibtex_key("Ashish Vaswani", 2017, "Attention Is All You Need") == "vaswani2017"
    assert 'attachment; filename="library.csv"' == _attachment("", "library.csv", "text/csv").headers["content-disposition"]


def test_arabic_titles_and_author_names_survive_export_keys():
    title = "الذكاء الاصطناعي"

    assert _slug(title) == "الذكاء-الاصطناعي"
    assert markdown_filename({"title": title}) == "الذكاء-الاصطناعي.md"
    assert _bibtex_key("أحمد علي", 2024, title) == "علي2024"
    assert "@article{علي2024," in to_bibtex(
        [{"title": title, "resolved_authors": "أحمد علي", "resolved_year": 2024}]
    )


def test_csv_starts_with_a_utf8_bom_and_keeps_arabic_text():
    csv_text = to_library_csv([{"title": "الذكاء الاصطناعي", "status": "complete"}])

    assert csv_text.startswith("\ufeff")
    assert "الذكاء الاصطناعي" in csv_text
    assert csv_text.encode("utf-8").startswith(b"\xef\xbb\xbf")


def test_unicode_attachment_has_rfc5987_name_and_ascii_fallback():
    response = _attachment("content", "الذكاء-الاصطناعي.md", "text/markdown")
    disposition = response.headers["content-disposition"]

    assert 'filename="export.md"' in disposition
    assert "filename*=UTF-8''%D8%A7%D9%84%D8%B0%D9%83%D8%A7%D8%A1-%D8%A7%D9%84%D8%A7%D8%B5%D8%B7%D9%86%D8%A7%D8%B9%D9%8A.md" in disposition
