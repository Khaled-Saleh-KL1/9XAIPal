"""article_crawl.py: the raw-HTML-snapshot sanitizer, same-site link
discovery, and the bounded crawl that ties them together.

Same convention as test_article_extraction.py: real loopback/private
addresses for the SSRF cases (no mocking needed — 127.0.0.1 is genuinely
unsafe everywhere), fetch_resource mocked for the crawl-mechanics cases
(depth/page/time caps, off-site filtering) since those are about this
module's own bookkeeping, not the network.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.services.article_crawl import (
    CrawledPage,
    crawl_article_pages,
    save_crawled_pages,
    sanitize_html,
    _page_title,
    _same_site_links,
)
from app.services.article_extraction import ArticleExtractionError, FetchedResource
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


# ── _page_title ──────────────────────────────────────────────────────────────

def test_page_title_reads_title_tag():
    assert _page_title("<html><head><title> Real Title </title></head></html>", "fallback") == "Real Title"


def test_page_title_falls_back_when_missing():
    assert _page_title("<html><body>no title here</body></html>", "https://x/y") == "https://x/y"


# ── _same_site_links ─────────────────────────────────────────────────────────

_LINKS_PAGE = """<html><body>
<a href="/docs/chapter2">Chapter 2</a>
<a href="https://docs.example.com/chapter3">Chapter 3 (absolute, same host)</a>
<a href="https://other-site.example/x">Off-site</a>
<a href="#section">Anchor only</a>
<a href="mailto:a@b.com">Email</a>
<a href="javascript:void(0)">JS link</a>
<a href="/docs/chapter2">Duplicate of chapter2</a>
</body></html>"""


def test_same_site_links_keeps_only_matching_hostname():
    links = _same_site_links(_LINKS_PAGE, "https://docs.example.com/intro", "docs.example.com")
    assert "https://docs.example.com/docs/chapter2" in links
    assert "https://docs.example.com/chapter3" in links
    assert not any("other-site.example" in link for link in links)


def test_same_site_links_drops_fragment_mailto_and_javascript():
    links = _same_site_links(_LINKS_PAGE, "https://docs.example.com/intro", "docs.example.com")
    assert not any(link.startswith(("mailto:", "javascript:")) for link in links)
    assert "https://docs.example.com/intro" not in links  # the #section self-link


def test_same_site_links_dedupes():
    links = _same_site_links(_LINKS_PAGE, "https://docs.example.com/intro", "docs.example.com")
    assert links.count("https://docs.example.com/docs/chapter2") == 1


# ── crawl_article_pages: SSRF guard applies per hop, not just the root ─────
# Real loopback address, no mocking — matches test_article_extraction.py's
# own convention for proving the guard is real.

def test_crawl_skips_a_same_site_link_that_is_actually_loopback():
    """The root URL is itself 127.0.0.1 (so its hostname legitimately matches
    a link also on 127.0.0.1 — same-site scoping alone would happily follow
    it), but fetch_resource must still refuse it, exactly like it would
    refuse it as a root URL. A followed link is not a trusted link."""
    root_html = '<html><body><a href="http://127.0.0.1/admin">admin</a></body></html>'
    pages = crawl_article_pages("http://127.0.0.1/", root_html)
    # Only the root page (persisted directly from root_html, no fetch) is
    # present — the loopback link was discovered, attempted, and rejected.
    assert len(pages) == 1
    assert pages[0].depth == 0


# ── crawl_article_pages: bookkeeping (mocked fetch_resource) ────────────────

def _page(html: str, url: str) -> FetchedResource:
    return FetchedResource(content=html.encode(), content_type="text/html", final_url=url)


def test_crawl_includes_root_at_depth_zero_with_no_extra_fetch():
    root_html = "<html><body>no links here</body></html>"
    with patch.object(article_crawl, "fetch_resource") as mock_fetch:
        pages = crawl_article_pages("https://docs.example.com/intro", root_html)
    mock_fetch.assert_not_called()
    assert len(pages) == 1
    assert pages[0].url == "https://docs.example.com/intro"
    assert pages[0].depth == 0


def test_crawl_follows_same_site_links_and_skips_off_site():
    root_html = (
        '<html><body>'
        '<a href="https://docs.example.com/ch1">Ch1</a>'
        '<a href="https://other.example/x">Off-site</a>'
        '</body></html>'
    )
    ch1_html = "<html><body>chapter 1, no further links</body></html>"

    def fake_fetch(url):
        if url == "https://docs.example.com/ch1":
            return _page(ch1_html, url)
        raise AssertionError(f"should never fetch off-site or unexpected url: {url}")

    with patch.object(article_crawl, "fetch_resource", side_effect=fake_fetch):
        pages = crawl_article_pages("https://docs.example.com/intro", root_html)

    urls = {p.url for p in pages}
    assert urls == {"https://docs.example.com/intro", "https://docs.example.com/ch1"}


def test_crawl_stops_at_max_crawl_pages(monkeypatch):
    monkeypatch.setattr(article_crawl, "MAX_CRAWL_PAGES", 2)
    root_html = "".join(
        f'<a href="https://docs.example.com/ch{i}">Ch{i}</a>' for i in range(10)
    )
    root_html = f"<html><body>{root_html}</body></html>"

    def fake_fetch(url):
        return _page("<html><body>a chapter, no further links</body></html>", url)

    with patch.object(article_crawl, "fetch_resource", side_effect=fake_fetch):
        pages = crawl_article_pages("https://docs.example.com/intro", root_html)

    assert len(pages) == 2  # root + exactly one followed link, cap respected


def test_crawl_respects_max_depth(monkeypatch):
    monkeypatch.setattr(article_crawl, "MAX_CRAWL_DEPTH", 1)
    root_html = '<html><body><a href="https://docs.example.com/ch1">Ch1</a></body></html>'
    ch1_html = '<html><body><a href="https://docs.example.com/ch1a">Ch1a</a></body></html>'

    def fake_fetch(url):
        if url == "https://docs.example.com/ch1":
            return _page(ch1_html, url)
        raise AssertionError(f"depth-2 page should never be fetched: {url}")

    with patch.object(article_crawl, "fetch_resource", side_effect=fake_fetch):
        pages = crawl_article_pages("https://docs.example.com/intro", root_html)

    urls = {p.url for p in pages}
    assert urls == {"https://docs.example.com/intro", "https://docs.example.com/ch1"}
    assert "https://docs.example.com/ch1a" not in urls


def test_crawl_skips_a_link_that_turns_out_to_be_a_pdf():
    root_html = '<html><body><a href="https://docs.example.com/paper.pdf">PDF</a></body></html>'
    pdf_resource = FetchedResource(
        content=b"%PDF-1.5 ...", content_type="application/pdf",
        final_url="https://docs.example.com/paper.pdf",
    )
    with patch.object(article_crawl, "fetch_resource", return_value=pdf_resource):
        pages = crawl_article_pages("https://docs.example.com/intro", root_html)

    assert len(pages) == 1  # root only — the PDF link isn't a page to sanitize/serve as HTML


def test_crawl_stops_when_time_budget_exceeded(monkeypatch):
    """Even with plenty of frontier left and well under MAX_CRAWL_PAGES, the
    wall-clock ceiling ends the crawl early — this is the guarantee that
    keeps a single --concurrency=1 worker from being tied up indefinitely."""
    monkeypatch.setattr(article_crawl, "CRAWL_TIME_BUDGET_SEC", 10.0)
    root_html = "".join(
        f'<a href="https://docs.example.com/ch{i}">Ch{i}</a>' for i in range(5)
    )
    root_html = f"<html><body>{root_html}</body></html>"

    clock = {"t": 0.0}

    def fake_monotonic():
        return clock["t"]

    def fake_fetch(url):
        clock["t"] += 20.0  # blow through the 10s budget on the very first fetch
        return _page("<html><body>a chapter, no further links</body></html>", url)

    monkeypatch.setattr(article_crawl.time, "monotonic", fake_monotonic)
    with patch.object(article_crawl, "fetch_resource", side_effect=fake_fetch):
        pages = crawl_article_pages("https://docs.example.com/intro", root_html)

    # Root always saved; at most one more page fetched before the budget
    # check (evaluated at the top of each loop iteration) ends the crawl.
    assert len(pages) <= 2
    assert len(pages) < 6  # nowhere near all 5 links + root


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


def test_save_crawled_pages_writes_files_and_rows(db_session_sync, tmp_path, monkeypatch):
    monkeypatch.setattr(article_crawl, "raw_snapshots_dir", lambda document_id=None: tmp_path / str(document_id))

    doc_id = uuid4()
    _insert_document(db_session_sync, doc_id)

    pages = [
        CrawledPage(url="https://docs.example.com/intro", title="Intro", html="<html>intro</html>", depth=0),
        CrawledPage(url="https://docs.example.com/ch1", title="Ch1", html="<html>ch1</html>", depth=1),
    ]
    saved = save_crawled_pages(db_session_sync, doc_id, pages)
    db_session_sync.commit()

    assert saved == 2
    rows = db_session_sync.execute(
        text("SELECT url, title, depth, storage_filename FROM raw_snapshot_pages WHERE document_id = :id ORDER BY depth"),
        {"id": doc_id},
    ).mappings().all()
    assert len(rows) == 2
    assert rows[0]["depth"] == 0 and rows[0]["url"] == "https://docs.example.com/intro"
    assert rows[1]["depth"] == 1 and rows[1]["url"] == "https://docs.example.com/ch1"

    for row in rows:
        html_path = (tmp_path / str(doc_id)) / row["storage_filename"]
        assert html_path.exists()


def test_save_crawled_pages_one_bad_page_does_not_lose_the_others(db_session_sync, tmp_path, monkeypatch):
    """A single page failing to persist (simulated here as an unwritable
    destination) must not lose every other already-fetched page — this is a
    best-effort save, matching crawl_article_pages' own partial-result
    stance."""
    monkeypatch.setattr(article_crawl, "raw_snapshots_dir", lambda document_id=None: tmp_path / str(document_id))

    doc_id = uuid4()
    _insert_document(db_session_sync, doc_id)

    good = CrawledPage(url="https://docs.example.com/intro", title="Intro", html="<html>intro</html>", depth=0)
    bad = CrawledPage(url="https://docs.example.com/ch1", title="Ch1", html="<html>ch1</html>", depth=1)

    real_write = article_crawl.uuid.uuid4
    calls = {"n": 0}

    def flaky_uuid4():
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk failure")
        return real_write()

    monkeypatch.setattr(article_crawl.uuid, "uuid4", flaky_uuid4)
    saved = save_crawled_pages(db_session_sync, doc_id, [good, bad])
    db_session_sync.commit()

    assert saved == 1
    rows = db_session_sync.execute(
        text("SELECT url FROM raw_snapshot_pages WHERE document_id = :id"), {"id": doc_id},
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["url"] == "https://docs.example.com/intro"
