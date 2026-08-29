"""article_extraction.py's own logic — SSRF rejection and the markdown/image
cleanup helpers. Real, deterministic loopback/private addresses are used for
the SSRF cases (127.0.0.1 resolves to loopback everywhere, no mocking
needed); the actual page-fetch + trafilatura path is exercised by
test_article_ingestion.py with extract_article mocked, and was additionally
verified against real, live pages during development (see the plan notes) —
that verification isn't something that belongs running on every CI run
against the open internet.
"""

import pytest

from app.services.article_extraction import (
    ArticleExtractionError,
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
