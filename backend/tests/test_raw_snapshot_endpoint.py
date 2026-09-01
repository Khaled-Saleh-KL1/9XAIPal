"""GET /{paper_id}/raw's decision logic and index-page rendering — the parts
of the endpoint pulled out into plain functions specifically so they're
testable without a DB or the FastAPI/ASGI stack (same reasoning as
_to_storage_path in notes.py and _pdf_name_from_url in pipeline_sync.py).

Ownership scoping (a cross-tenant document 404s here exactly like everywhere
else) is get_document's own guarantee, already covered by its existing
callers' tests — nothing raw-snapshot-specific to re-prove there.
"""

from app.api.v1.endpoints.documents import _raw_response_kind, _raw_snapshot_index_html


# ── _raw_response_kind ───────────────────────────────────────────────────────

def test_pdf_and_book_doc_kinds_always_serve_the_pdf_branch():
    assert _raw_response_kind("paper", [], None) == "pdf"
    assert _raw_response_kind("book", [{"id": 1}], "complete") == "pdf"


def test_article_with_no_pages_and_pending_crawl_is_pending():
    assert _raw_response_kind("article", [], "pending") == "pending"


def test_article_with_no_pages_and_no_crawl_in_flight_is_unavailable():
    assert _raw_response_kind("article", [], "complete") == "unavailable"
    assert _raw_response_kind("article", [], "failed") == "unavailable"
    assert _raw_response_kind("article", [], "none") == "unavailable"
    assert _raw_response_kind("article", [], None) == "unavailable"


def test_article_with_one_page_is_single():
    assert _raw_response_kind("article", [{"id": 1}], "complete") == "single"


def test_article_with_multiple_pages_is_index():
    assert _raw_response_kind("article", [{"id": 1}, {"id": 2}], "complete") == "index"


# ── _raw_snapshot_index_html ────────────────────────────────────────────────

async def test_index_html_links_to_each_page():
    doc = {"id": "doc-1", "title": "My Docs Import", "original_filename": "https://docs.example.com"}
    pages = [
        {"id": "p0", "url": "https://docs.example.com/", "title": "Intro", "depth": 0},
        {"id": "p1", "url": "https://docs.example.com/ch1", "title": "Chapter 1", "depth": 1},
    ]
    out = await _raw_snapshot_index_html(doc, pages)
    assert "/api/v1/papers/doc-1/raw/p0" in out
    assert "/api/v1/papers/doc-1/raw/p1" in out
    assert "Intro" in out
    assert "Chapter 1" in out
    assert "root page" in out


async def test_index_html_escapes_a_malicious_page_title():
    """A crawled page's title came from a third party (see article_crawl.py's
    _page_title) — this response is CSP-protected (script-src 'none') AND
    must not let attacker-controlled text become live markup regardless,
    matching this codebase's belt-and-suspenders approach elsewhere."""
    doc = {"id": "doc-1", "title": "Import", "original_filename": "x"}
    pages = [
        {"id": "p0", "url": "https://evil.example/", "title": "<script>alert(1)</script>", "depth": 0},
    ]
    out = await _raw_snapshot_index_html(doc, pages)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


async def test_index_html_escapes_a_malicious_document_title():
    doc = {"id": "doc-1", "title": '"><img src=x onerror=alert(1)>', "original_filename": "x"}
    pages = [{"id": "p0", "url": "https://x/", "title": "Page", "depth": 0}]
    out = await _raw_snapshot_index_html(doc, pages)
    assert "<img src=x onerror=alert(1)>" not in out
