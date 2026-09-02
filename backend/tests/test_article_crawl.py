"""article_crawl.py: the raw-HTML-snapshot sanitizer and its DB/disk save.

This module used to also crawl same-site linked pages (a "book-like"
multi-page docs site); that was dropped in favor of extracting the page's
own real hyperlinks into the article content instead (see
article_extraction.py's include_links=True) and letting the reader follow
one themselves — see article_crawl.py's module docstring. What's left here
is just: sanitize one already-fetched page, save it.
"""

from uuid import uuid4

from sqlalchemy import text

from app.services.article_crawl import (
    CrawledPage,
    save_crawled_pages,
    sanitize_html,
    snapshot_article_page,
    _page_title,
)
import app.services.article_crawl as article_crawl


# ── sanitize_html ────────────────────────────────────────────────────────────

_TABBED_PAGE = """<html><head><title>Docs</title>
<script>alert(1)</script>
<style>.tab-panel { display: none; } .tab-panel.active { display: block; }</style>
</head><body>
<div class="tab-panel active" onclick="steal()">Visible tab</div>
<div class="tab-panel">Hidden tab content trafilatura might miss</div>
<img src="/fig1.png" onerror="hack()">
<a href="javascript:alert(2)">bad link</a>
</body></html>"""


def test_sanitize_html_strips_script_tag():
    out = sanitize_html(_TABBED_PAGE, "https://docs.example.com/intro")
    assert "<script" not in out
    assert "alert(1)" not in out


def test_sanitize_html_strips_event_handler_attributes():
    out = sanitize_html(_TABBED_PAGE, "https://docs.example.com/intro")
    assert "onclick" not in out
    assert "onerror" not in out


def test_sanitize_html_neutralizes_javascript_url():
    out = sanitize_html(_TABBED_PAGE, "https://docs.example.com/intro")
    assert "javascript:" not in out


def test_sanitize_html_preserves_hidden_tab_panel_content():
    """The actual point of this feature: a JS/CSS-hidden tab panel's text is
    still readable in the saved DOM, even though it's invisible in a normal
    render — this is what lets a reader check whether trafilatura missed it."""
    out = sanitize_html(_TABBED_PAGE, "https://docs.example.com/intro")
    assert "Hidden tab content trafilatura might miss" in out


def test_sanitize_html_keeps_inline_style_block():
    out = sanitize_html(_TABBED_PAGE, "https://docs.example.com/intro")
    assert "display: none" in out


def test_sanitize_html_injects_base_href():
    out = sanitize_html(_TABBED_PAGE, "https://docs.example.com/intro")
    assert '<base href="https://docs.example.com/intro">' in out


def test_sanitize_html_strips_meta_refresh_redirect():
    page = '<html><head><meta http-equiv="refresh" content="0;url=http://evil.example"></head><body>hi</body></html>'
    out = sanitize_html(page, "https://example.com/x")
    assert "evil.example" not in out


def test_sanitize_html_strips_iframe_and_form():
    page = (
        '<html><body><iframe src="http://evil.example"></iframe>'
        '<form action="/steal"><input name="x"></form>ok</body></html>'
    )
    out = sanitize_html(page, "https://example.com/x")
    assert "<iframe" not in out
    assert "<form" not in out
    assert "evil.example" not in out


def test_sanitize_html_injects_a_readability_stylesheet():
    """The Cleaner strips every <link rel="stylesheet"> (see its own
    comment) — without an app-authored fallback, a raw snapshot would
    render in the browser's bare default styling. This is the fix, not
    an attempt to reproduce the original site's own design."""
    out = sanitize_html("<html><body><p>hi</p></body></html>", "https://example.com/x")
    assert "<style>" in out
    assert "max-width: 720px" in out
    assert "font-family" in out


def test_sanitize_html_readability_css_comes_after_original_style_block():
    """Cascade order matters: this app's baseline typography must come AFTER
    any surviving original <style> block in document order, so it wins for
    equal-specificity element selectors (body, p, ...) — a more specific
    original rule (a class selector) still applies regardless, since
    specificity outranks order."""
    page = '<html><head><style>body { color: red; }</style></head><body><p>hi</p></body></html>'
    out = sanitize_html(page, "https://example.com/x")
    original_pos = out.index("color: red")
    readability_pos = out.index("max-width: 720px")
    assert readability_pos > original_pos


# ── inline layout geometry (the unreadable-sliver bug) ──────────────────────

_CAROUSEL = (
    '<html><body>'
    '<div class="viewport" style="inset-inline-start: -524.5px; '
    'padding-inline: 524.5px 319.5px; scroll-padding-inline: 524.5px 319.5px;">'
    '<p>Token-efficient long-form video analysis</p>'
    '</div></body></html>'
)


def test_sanitize_html_drops_js_computed_horizontal_geometry():
    """Reproduced from a real page (blog.google's agentic-video post): a
    carousel's own JavaScript had hard-coded 844px of horizontal padding for
    a full-width layout. Inside the snapshot's 720px reading column that
    squeezed the caption to 67px wide and 159px tall — one or two words per
    line straight down the page. An inline declaration outranks any
    stylesheet, so _READABILITY_CSS cannot fix this from the outside."""
    out = sanitize_html(_CAROUSEL, "https://example.com/x")
    assert "padding-inline" not in out
    assert "inset-inline-start" not in out
    assert "scroll-padding-inline" not in out
    # the content itself is untouched
    assert "Token-efficient long-form video analysis" in out


def test_sanitize_html_keeps_non_layout_inline_styles():
    """Only horizontal geometry goes. Colour, weight and display are not
    what breaks the reading column, and dropping them wholesale would change
    the page more than it needs to be changed."""
    page = '<html><body><p style="color: red; font-weight: 700; display: block;">hi</p></body></html>'
    out = sanitize_html(page, "https://example.com/x")
    assert "color: red" in out
    assert "font-weight: 700" in out
    assert "display: block" in out


def test_sanitize_html_keeps_visually_hidden_pattern_hidden():
    """The SVG-sprite / screen-reader hiding idiom is
    `position:absolute; width:0; height:0; overflow:hidden`. `width` is
    stripped as geometry, but `height:0` + `overflow:hidden` still collapse
    it — so those definitions don't suddenly splash across the page. This is
    why the attribute is filtered per-declaration rather than removed
    wholesale."""
    page = (
        '<html><body><svg style="position:absolute; width:0; height:0; overflow:hidden;">'
        '<defs></defs></svg></body></html>'
    )
    out = sanitize_html(page, "https://example.com/x")
    assert "width:0" not in out and "width: 0" not in out
    assert "height:0" in out or "height: 0" in out
    assert "overflow:hidden" in out or "overflow: hidden" in out
    assert "position:absolute" in out or "position: absolute" in out


def test_sanitize_html_removes_the_attribute_when_nothing_survives():
    page = '<html><body><div style="padding-inline: 500px; width: 900px;">x</div></body></html>'
    out = sanitize_html(page, "https://example.com/x")
    assert 'style=""' not in out
    assert "padding-inline" not in out
    assert "900px" not in out


# ── _page_title ──────────────────────────────────────────────────────────────

def test_page_title_reads_title_tag():
    assert _page_title("<html><head><title> Real Title </title></head></html>", "fallback") == "Real Title"


def test_page_title_falls_back_when_missing():
    assert _page_title("<html><body>no title here</body></html>", "https://x/y") == "https://x/y"


# ── snapshot_article_page ────────────────────────────────────────────────────

def test_snapshot_article_page_returns_one_sanitized_page():
    url = "https://docs.example.com/intro"
    pages = snapshot_article_page(url, _TABBED_PAGE)

    assert len(pages) == 1
    page = pages[0]
    assert page.url == url
    assert page.depth == 0
    assert page.title == "Docs"
    assert "<script" not in page.html
    assert "Hidden tab content trafilatura might miss" in page.html


def test_snapshot_article_page_no_network_call():
    """No fetch happens here at all — the caller already has the HTML in
    hand (see extraction/pipeline_sync.py::run_article_pipeline_sync)."""
    pages = snapshot_article_page("https://example.com/x", "<html><body>hi</body></html>")
    assert len(pages) == 1


# ── save_crawled_pages ───────────────────────────────────────────────────────

def _insert_document(session, doc_id):
    session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, doc_kind, status) "
            "VALUES (:id, :filename, :filename, 'article', 'queued')"
        ),
        {"id": doc_id, "filename": f"{doc_id.hex}.html"},
    )
    session.commit()


def test_save_crawled_pages_writes_file_and_row(db_session_sync, tmp_path, monkeypatch):
    monkeypatch.setattr(article_crawl, "raw_snapshots_dir", lambda document_id=None: tmp_path / str(document_id))

    doc_id = uuid4()
    _insert_document(db_session_sync, doc_id)

    pages = [CrawledPage(url="https://docs.example.com/intro", title="Intro", html="<html>intro</html>", depth=0)]
    saved = save_crawled_pages(db_session_sync, doc_id, pages)
    db_session_sync.commit()

    assert saved == 1
    rows = db_session_sync.execute(
        text("SELECT url, title, depth, storage_filename FROM raw_snapshot_pages WHERE document_id = :id"),
        {"id": doc_id},
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["depth"] == 0 and rows[0]["url"] == "https://docs.example.com/intro"

    html_path = (tmp_path / str(doc_id)) / rows[0]["storage_filename"]
    assert html_path.exists()


def test_save_crawled_pages_failure_is_logged_and_returns_zero(db_session_sync, tmp_path, monkeypatch):
    """A page failing to persist (simulated here as an unwritable
    destination) must not raise — best-effort, matching
    run_article_pipeline_sync's own "a raw-snapshot failure must never
    affect the article itself" invariant."""
    monkeypatch.setattr(article_crawl, "raw_snapshots_dir", lambda document_id=None: tmp_path / str(document_id))

    doc_id = uuid4()
    _insert_document(db_session_sync, doc_id)

    bad = CrawledPage(url="https://docs.example.com/ch1", title="Ch1", html="<html>ch1</html>", depth=0)

    def flaky_uuid4():
        raise OSError("simulated disk failure")

    monkeypatch.setattr(article_crawl.uuid, "uuid4", flaky_uuid4)
    saved = save_crawled_pages(db_session_sync, doc_id, [bad])
    db_session_sync.commit()

    assert saved == 0
    rows = db_session_sync.execute(
        text("SELECT url FROM raw_snapshot_pages WHERE document_id = :id"), {"id": doc_id},
    ).mappings().all()
    assert len(rows) == 0


# ── videos in the snapshot ──────────────────────────────────────────────────

def test_sanitize_html_gives_videos_controls():
    """A page that plays video drives it from JavaScript and ships the
    element with no controls of its own. Strip the scripts — which this
    sanitizer must — and what's left is a still poster the reader can never
    start. Every <video> on the real page that surfaced this had a poster,
    `muted`, `playsinline`, and no controls."""
    page = (
        '<html><body><video preload="auto" autoplay muted playsinline '
        'poster="https://cdn.example.com/f.jpg">'
        '<source src="https://cdn.example.com/v.mp4" type="video/mp4">'
        '</video></body></html>'
    )
    out = sanitize_html(page, "https://example.com/x")
    assert "controls" in out
    assert 'preload="metadata"' in out
    assert "autoplay" not in out
    # the source itself is untouched
    assert "https://cdn.example.com/v.mp4" in out


def test_sanitize_html_drops_crossorigin_so_video_can_actually_load():
    """crossorigin="anonymous" makes the browser fetch media in CORS mode,
    which fails outright unless the host sends Access-Control-Allow-Origin.
    Verified against the real video host: no CORS header at all, so every
    source failed with net::ERR_FAILED and the element sat at its default
    300x150 with networkState NO_SOURCE. Plain playback needs no CORS."""
    page = (
        '<html><body><video crossorigin="anonymous">'
        '<source src="https://cdn.example.com/v.mp4" type="video/mp4">'
        '</video></body></html>'
    )
    out = sanitize_html(page, "https://example.com/x")
    assert "crossorigin" not in out
    assert "https://cdn.example.com/v.mp4" in out


def test_sanitize_html_drops_a_poster_that_is_not_an_image():
    page = (
        '<html><body><video poster="https://example.com/the/article/" '
        'src="https://cdn.example.com/v.mp4"></video></body></html>'
    )
    out = sanitize_html(page, "https://example.com/x")
    assert "poster" not in out


def test_sanitize_html_keeps_a_real_image_poster():
    page = (
        '<html><body><video poster="https://cdn.example.com/frame.jpg" '
        'src="https://cdn.example.com/v.mp4"></video></body></html>'
    )
    assert "frame.jpg" in sanitize_html(page, "https://example.com/x")


# ── lazy-loaded images ──────────────────────────────────────────────────────

def test_sanitize_html_upgrades_a_lazy_placeholder_to_the_real_image():
    """Lazy-loading ships a deliberately tiny image in `src` and keeps the
    real one for the page's own JavaScript to swap in. Strip the scripts —
    which this sanitizer must — and the swap never happens. On the real page
    four quote cards were left showing a 100px-wide placeholder with the
    1000px version sitting unused in data-loading."""
    page = (
        '<html><body><img src="https://cdn.example.com/thumb.width-100.webp" '
        """data-loading='{"mobile": "https://cdn.example.com/m.width-500.webp", """
        """"desktop": "https://cdn.example.com/d.width-1000.webp"}'>"""
        '</body></html>'
    )
    out = sanitize_html(page, "https://example.com/x")
    assert "d.width-1000.webp" in out
    assert "thumb.width-100.webp" not in out
    assert "data-loading" not in out


def test_sanitize_html_prefers_the_widest_srcset_candidate():
    page = (
        '<html><body><img src="https://cdn.example.com/tiny.jpg" '
        'data-srcset="https://cdn.example.com/a.jpg 300w, '
        'https://cdn.example.com/b.jpg 1200w, https://cdn.example.com/c.jpg 800w">'
        '</body></html>'
    )
    out = sanitize_html(page, "https://example.com/x")
    assert "b.jpg" in out
    assert "tiny.jpg" not in out


def test_sanitize_html_keeps_native_lazy_loading():
    """`loading="lazy"` is native browser behaviour that needs no
    JavaScript, so unlike the data-* placeholders it still works in a
    snapshot and still avoids pulling every image on a long page."""
    page = '<html><body><img src="https://cdn.example.com/a.jpg" loading="lazy"></body></html>'
    assert 'loading="lazy"' in sanitize_html(page, "https://example.com/x")


def test_sanitize_html_leaves_an_ordinary_image_alone():
    page = '<html><body><img src="https://cdn.example.com/plain.jpg" alt="x"></body></html>'
    out = sanitize_html(page, "https://example.com/x")
    assert "plain.jpg" in out
