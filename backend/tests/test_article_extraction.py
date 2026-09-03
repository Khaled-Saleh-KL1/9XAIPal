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

import httpx
import pytest

from app.extraction.pipeline_sync import _pdf_name_from_url
from app.services.article_extraction import (
    ArticleExtractionError,
    FetchedResource,
    _clean_markdown,
    _drop_image_refs,
    _image_urls_in,
    extract_article,
    extract_article_from_html,
    fetch_resource,
)


# ── extract_article_from_html: trafilatura's metadata frontmatter leak ──────

def test_extract_article_from_html_does_not_leak_yaml_frontmatter_into_the_body():
    """Real bug, found in every article in the library: trafilatura's
    with_metadata=True prepends a YAML block ("---\ntitle: ...\nauthor:
    ...\n---") directly into the markdown it returns. That block was landing
    as the article's own first chunk — rendered as markdown, a run of
    non-blank "key: value" lines with no blank line between them collapses
    into a single unreadable paragraph. The title this app actually uses
    already comes from the separate extract_metadata() call, so the
    frontmatter block bought nothing.
    """
    html = """
    <html><head>
      <title>A Real Article Title</title>
      <meta name="author" content="Jane Doe">
      <meta property="article:published_time" content="2026-01-15">
    </head><body><article>
      <h1>A Real Article Title</h1>
      <p>""" + ("This is a real paragraph of article prose. " * 10) + """</p>
      <p>""" + ("A second paragraph with enough length to clear the minimum. " * 10) + """</p>
    </article></body></html>
    """
    article = extract_article_from_html(html, "https://example.com/a-real-article")

    assert not article.markdown.lstrip().startswith("---")
    assert "author:" not in article.markdown
    assert "sitename:" not in article.markdown
    assert article.title == "A Real Article Title"
    assert "real paragraph of article prose" in article.markdown


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


# ── Bot-protection 403/429s get an honest message ──────────────────────────
# A real reader hit this against a Medium article: the raw error was a bare
# "Client error '403 Forbidden'", which reads like our bug rather than what
# it actually was — Medium is behind Cloudflare, and the response was a JS
# challenge page (`cf-mitigated: challenge`), not a real "you may not have
# this". No User-Agent or header trick clears that; only saying so plainly
# is actually honest.

def _status_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://example.com/article")
    return httpx.Response(status_code, headers=headers or {}, request=request)


def test_fetch_resource_403_from_cloudflare_names_it():
    resp = _status_response(403, headers={"cf-mitigated": "challenge", "server": "cloudflare"})
    with patch("app.services.article_extraction.safe_send_sync", return_value=resp):
        with pytest.raises(ArticleExtractionError) as exc:
            fetch_resource("https://medium.com/@x/some-article")
    msg = str(exc.value)
    assert "blocked the request" in msg
    assert "Cloudflare" in msg
    assert "403" not in msg  # the honest explanation, not the raw status line


def test_fetch_resource_429_without_cloudflare_headers_still_names_bot_protection():
    resp = _status_response(429)
    with patch("app.services.article_extraction.safe_send_sync", return_value=resp):
        with pytest.raises(ArticleExtractionError) as exc:
            fetch_resource("https://example.com/rate-limited")
    msg = str(exc.value)
    assert "blocked the request" in msg
    assert "Cloudflare" not in msg  # no evidence it specifically was — don't overclaim


def test_fetch_resource_404_keeps_the_generic_message():
    """A genuine 404 isn't bot protection — the specific message must not
    fire for every non-2xx status, only the ones that actually mean it."""
    resp = _status_response(404)
    with patch("app.services.article_extraction.safe_send_sync", return_value=resp):
        with pytest.raises(ArticleExtractionError) as exc:
            fetch_resource("https://example.com/gone")
    msg = str(exc.value)
    assert "blocked the request" not in msg
    assert "Couldn't fetch that page" in msg


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


# ── videos: the one real content trafilatura reliably discards ──────────────

_VIDEO_PAGE = """
<html><body><article>
  <h2>Benchmarks</h2>
  <p>Some prose about benchmarks that is long enough to survive extraction,
     repeated for length. Some prose about benchmarks that is long enough.</p>
  <h2>Capabilities and use cases</h2>
  <p>More prose here, also long enough to be kept by the extractor rather
     than discarded as boilerplate chrome. More prose here, long enough.</p>
  <div class="fancy-carousel-widget">
    <video poster="https://cdn.example.com/frame.jpg" aria-label="a demo of the feature">
      <source src="https://cdn.example.com/demo.mp4" type="video/mp4">
    </video>
  </div>
</article></body></html>
"""


def test_extract_videos_finds_source_heading_and_caption():
    from app.services.article_extraction import _extract_videos

    videos = _extract_videos(_VIDEO_PAGE)
    assert len(videos) == 1
    v = videos[0]
    assert v["src"] == "https://cdn.example.com/demo.mp4"
    assert v["heading"] == "Capabilities and use cases"
    assert v["caption"] == "a demo of the feature"
    assert v["poster"] == "https://cdn.example.com/frame.jpg"


def test_extract_videos_rejects_a_poster_that_is_not_an_image():
    """Real case: the upstream scraper resolved an empty poster attribute
    against the page URL, leaving every video pointing at an HTML document
    as its poster frame — one guaranteed-failed request per video."""
    from app.services.article_extraction import _extract_videos

    page = (
        '<html><body><video poster="https://example.com/some/article/" '
        'src="https://cdn.example.com/v.mp4"></video></body></html>'
    )
    assert _extract_videos(page)[0]["poster"] == ""


def test_extract_videos_ignores_a_video_with_no_usable_source():
    from app.services.article_extraction import _extract_videos

    assert _extract_videos("<html><body><video></video></body></html>") == []
    # a relative/blob source is not something the reader's browser can fetch
    page = '<html><body><video src="blob:abc123"></video></body></html>'
    assert _extract_videos(page) == []


def test_extract_videos_finds_a_uni_media_video_with_no_video_tag():
    """Real case (blog.google's "Introducing Gemini 3.8 Flash and 3.8 Flash
    Cyber" post): the site's own media-carousel component ships a video as
    <uni-media or-mp4-video-url="..."> with no <video>/<source> anywhere —
    the real element only appears once the page's own JS upgrades the
    custom tag, which this static extractor never runs."""
    from app.services.article_extraction import _extract_videos

    page = (
        "<html><body><article>"
        "<h2>Built with Gemini</h2>"
        '<p>Prose long enough to survive extraction as a real heading '
        "anchor for the video below it, repeated for length here.</p>"
        '<uni-media or-mp4-video-url="https://cdn.example.com/demo.mp4" '
        'alt-text="a demo of the feature" video-title="Demo"></uni-media>'
        "</article></body></html>"
    )
    videos = _extract_videos(page)
    assert len(videos) == 1
    v = videos[0]
    assert v["src"] == "https://cdn.example.com/demo.mp4"
    assert v["heading"] == "Built with Gemini"
    assert v["caption"] == "a demo of the feature"
    assert v["poster"] == ""


def test_extract_videos_ignores_a_uni_media_slide_with_no_video():
    """The same component is reused for plain image slides, always carrying
    the attribute but empty — must not be mistaken for a video."""
    from app.services.article_extraction import _extract_videos

    page = '<html><body><uni-media or-mp4-video-url=""></uni-media></body></html>'
    assert _extract_videos(page) == []


def test_splice_videos_puts_each_video_under_its_own_heading():
    from app.services.article_extraction import splice_videos_into_markdown

    md = "# Title\n\n## Benchmarks\n\nnumbers here\n\n## Capabilities and use cases\n\ntext here\n"
    out = splice_videos_into_markdown(md, _VIDEO_PAGE)

    assert "<video" in out
    # it belongs to the section it appeared under, not the one before it
    caps = out.index("## Capabilities and use cases")
    assert out.index("<video") > caps
    assert "*a demo of the feature*" in out


def test_splice_videos_appends_when_the_section_did_not_survive():
    """A video whose whole section was discarded still reaches the reader —
    at the end, rather than not at all."""
    from app.services.article_extraction import splice_videos_into_markdown

    md = "# Title\n\n## Something Else\n\nonly this section survived\n"
    out = splice_videos_into_markdown(md, _VIDEO_PAGE)
    assert "<video" in out
    assert out.index("<video") > out.index("only this section survived")


def test_splice_videos_is_a_no_op_without_video():
    from app.services.article_extraction import splice_videos_into_markdown

    md = "# Title\n\ntext\n"
    assert splice_videos_into_markdown(md, "<html><body><p>no video</p></body></html>") == md


def test_emitted_video_markup_never_autoplays():
    from app.services.article_extraction import _video_markup

    markup = _video_markup(
        {"src": "https://cdn.example.com/v.mp4", "poster": "", "caption": "", "heading": ""}
    )
    assert "controls" in markup
    assert 'preload="metadata"' in markup
    assert "autoplay" not in markup


def test_chunker_keeps_video_markup_out_of_the_paragraph_split():
    """The text branch of the markdown chunker rebuilds each chunk's
    markdown from `plain`, and `plain` has had every HTML tag stripped (that
    is what keeps table `colspan`/`rowspan` out of embeddings). A video
    block routed through there loses its tag from BOTH fields, leaving the
    reader a bare caption describing a video that isn't there."""
    from app.extraction.chunker import create_chunks_from_markdown

    md = (
        "# Title\n\n## Capabilities\n\nProse long enough to count as a real "
        "paragraph of body text here.\n\n"
        '<video controls preload="metadata" src="https://cdn.example.com/d.mp4"></video>\n\n'
        "*a caption for it*\n"
    )
    chunks = create_chunks_from_markdown(md)

    assert any("<video" in c["markdown"] for c in chunks), "video markup lost"
    # ...but never in the text that gets embedded
    assert not any("<video" in (c["plain_text"] or "") for c in chunks)
