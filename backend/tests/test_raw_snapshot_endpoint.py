"""GET /{paper_id}/raw's decision logic — pulled out into a plain function
specifically so it's testable without a DB or the FastAPI/ASGI stack (same
reasoning as _to_storage_path in notes.py and _pdf_name_from_url in
pipeline_sync.py).

Only two real cases now that a raw snapshot is a single page saved inline
during import, not a background crawl that could still be "pending" or
produce more than one page — see article_crawl.py's module docstring for
why that idea was dropped.

Ownership scoping (a cross-tenant document 404s here exactly like everywhere
else) is get_document's own guarantee, already covered by its existing
callers' tests — nothing raw-snapshot-specific to re-prove there.
"""

from app.api.v1.endpoints.documents import _raw_response_kind


def test_pdf_and_book_doc_kinds_always_serve_the_pdf_branch():
    assert _raw_response_kind("paper", []) == "pdf"
    assert _raw_response_kind("book", [{"id": 1}]) == "pdf"


def test_article_with_no_saved_page_is_unavailable():
    assert _raw_response_kind("article", []) == "unavailable"


def test_article_with_its_one_page_is_single():
    assert _raw_response_kind("article", [{"id": 1}]) == "single"
