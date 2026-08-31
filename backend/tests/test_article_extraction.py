"""article_extraction.py's own logic — SSRF rejection and the markdown/image
cleanup helpers. Real, deterministic loopback/private addresses are used for
the SSRF cases (127.0.0.1 resolves to loopback everywhere, no mocking
needed); the actual page-fetch + trafilatura path is exercised by
test_article_ingestion.py with extract_article mocked, and was additionally
verified against real, live pages during development (see the plan notes) —
that verification isn't something that belongs running on every CI run
against the open internet.
"""

from unittest.mock import patch

import pytest

from app.extraction.pipeline_sync import _pdf_name_from_url
from app.services.article_extraction import (
    ArticleExtractionError,
    FetchedResource,
    _clean_markdown,
    _drop_image_refs,
    _image_urls_in,
    extract_article,
)


def test_extract_article_rejects_non_http_scheme():
    with pytest.raises(ArticleExtractionError):
        extract_article("ftp://example.com/file")


def test_extract_article_rejects_loopback():
    with pytest.raises(ArticleExtractionError):
        extract_article("http://127.0.0.1/admin")


def test_extract_article_rejects_private_network():
    with pytest.raises(ArticleExtractionError):
        extract_article("http://192.168.1.1/")


def test_extract_article_rejects_link_local():
    # The cloud-metadata address (169.254.169.254) is exactly the kind of
    # target an SSRF guard exists to keep this server away from.
    with pytest.raises(ArticleExtractionError):
        extract_article("http://169.254.169.254/latest/meta-data/")


def test_clean_markdown_strips_known_noise():
    md = "# Title\n\nAppearance\n\nReal content here.\n\n[edit]\n\nMore content."
    cleaned = _clean_markdown(md)
    assert "Appearance" not in cleaned
    assert "[edit]" not in cleaned
    assert "Real content here." in cleaned
    assert "More content." in cleaned


def test_image_urls_in_finds_and_dedupes():
    md = "![a](https://x.com/a.png) text ![b](https://x.com/b.png) ![a again](https://x.com/a.png)"
    assert _image_urls_in(md) == ["https://x.com/a.png", "https://x.com/b.png"]


def test_drop_image_refs_removes_only_the_dropped_ones():
    md = "before ![keep](https://x.com/keep.png) middle ![drop](https://x.com/drop.png) after"
    result = _drop_image_refs(md, {"https://x.com/drop.png"})
    assert "keep.png" in result
    assert "drop.png" not in result
    assert "before" in result and "middle" in result and "after" in result


# ── PDF-by-link detection ───────────────────────────────────────────────────
# A pasted arXiv/journal PDF URL used to reach trafilatura (a static-HTML
# extractor), produce nothing, and fail with a message blaming a login or
# JavaScript. fetch_resource reports what a link actually is so the pipeline
# can route it to MinerU instead.

def _resource(content=b"", content_type="text/html"):
    return FetchedResource(content=content, content_type=content_type, final_url="https://x/y")


def test_is_pdf_by_content_type():
    assert _resource(b"%PDF-1.5 ...", "application/pdf").is_pdf


def test_is_pdf_by_magic_bytes_when_content_type_lies():
    """A server can serve a PDF as octet-stream or omit the type entirely;
    the magic bytes are the ground truth (/upload's own guard reads them for
    the same reason)."""
    assert _resource(b"%PDF-1.7\n1 0 obj", "application/octet-stream").is_pdf
    assert _resource(b"%PDF-1.4", "").is_pdf


def test_html_is_not_pdf():
    assert not _resource(b"<!DOCTYPE html><html>", "text/html").is_pdf


def test_pdf_content_type_with_charset_parameter_still_detected():
    """Content-Type arrives as e.g. 'application/pdf; charset=binary' — the
    parameter is stripped before comparison, so this must not miss."""
    assert _resource(b"%PDF-1.5", "application/pdf").is_pdf


def test_extract_article_on_a_pdf_says_the_real_reason():
    """Not 'it may require a login, a subscription, or JavaScript' — that
    message was the actual bug a reader saw when pasting an arXiv PDF."""
    pdf = _resource(b"%PDF-1.5 body", "application/pdf")
    with patch("app.services.article_extraction.fetch_resource", return_value=pdf):
        with pytest.raises(ArticleExtractionError) as exc:
            extract_article("https://arxiv.org/pdf/1706.03762")
    assert "PDF" in str(exc.value)
    assert "JavaScript" not in str(exc.value)


# ── original_filename derived from a PDF URL ────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    # arXiv's canonical PDF link has no .pdf suffix at all.
    ("https://arxiv.org/pdf/1706.03762", "1706.03762.pdf"),
    ("https://arxiv.org/pdf/2103.00020v1.pdf", "2103.00020v1.pdf"),
    # Nothing useful in the path — fall back to the host, never a blank row.
    ("https://example.com/", "example.com.pdf"),
    ("https://example.com", "example.com.pdf"),
])
def test_pdf_name_from_url(url, expected):
    assert _pdf_name_from_url(url) == expected
