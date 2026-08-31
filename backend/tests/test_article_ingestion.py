"""run_article_pipeline_sync: the third pipeline (URL -> markdown -> chunks),
sharing _finish_ingestion with the PDF pipeline but never its extraction step.

article_extraction's fetch is mocked here — no live network call belongs in
the regular suite. Its own fetch/filter logic is covered separately
(test_article_extraction.py) and was additionally verified against real,
live pages during development (see the plan notes).

The mocked seam is fetch_resource + extract_article_from_html rather than
extract_article: the pipeline has to see what a URL turned out to be before
it can choose a pipeline, since a PDF link goes to MinerU instead.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.extraction.pipeline_sync import run_article_pipeline_sync
from app.services.article_extraction import (
    ArticleExtraction,
    ArticleExtractionError,
    FetchedResource,
)


def _html_resource(url="https://example.com/some-article"):
    return FetchedResource(
        content=b"<html><body>irrelevant, extraction is mocked</body></html>",
        content_type="text/html",
        final_url=url,
    )


def _insert_document_and_job(session, doc_id, job_id):
    session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, doc_kind, source_url, status) "
            "VALUES (:id, :filename, :url, 'article', :url, 'queued')"
        ),
        {"id": doc_id, "filename": f"{doc_id.hex}.html", "url": "https://example.com/some-article"},
    )
    session.execute(
        text("INSERT INTO ingestion_jobs (id, document_id, status) VALUES (:id, :doc_id, 'queued')"),
        {"id": job_id, "doc_id": doc_id},
    )
    session.commit()


def test_run_article_pipeline_success(db_session_sync):
    doc_id = uuid4()
    job_id = uuid4()
    _insert_document_and_job(db_session_sync, doc_id, job_id)

    fake = ArticleExtraction(
        title="A Real Page Title",
        markdown=(
            "# A Real Page Title\n\n"
            "Some opening paragraph about the topic at hand.\n\n"
            "## A Section\n\n"
            "More text here, with a figure below.\n\n"
            "![a photo](photo.jpg)\n"
        ),
        asset_map={"photo.jpg": "https://cdn.example.com/img/photo.jpg"},
    )

    with patch(
        "app.services.article_extraction.fetch_resource", return_value=_html_resource()
    ), patch(
        "app.services.article_extraction.extract_article_from_html", return_value=fake
    ):
        run_article_pipeline_sync(
            db_session_sync,
            document_id=doc_id,
            job_id=job_id,
            url="https://example.com/some-article",
        )

    doc = db_session_sync.execute(
        text("SELECT * FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().one()

    # The placeholder original_filename (the URL) is overwritten with the
    # real page title once extraction has actually run.
    assert doc["original_filename"] == "A Real Page Title"
    assert doc["extractor"] == "trafilatura"
    assert doc["doc_kind"] == "article"
    assert doc["source_url"] == "https://example.com/some-article"
    # No PDF behind an article — page_count is never set.
    assert doc["page_count"] is None
    assert doc["status"] in ("processing", "complete")

    chunks = db_session_sync.execute(
        text("SELECT * FROM chunks WHERE document_id = :id ORDER BY sequence_id"),
        {"id": doc_id},
    ).mappings().all()
    assert len(chunks) >= 2
    assert any(c["chunk_type"] == "heading" for c in chunks)

    assets = db_session_sync.execute(
        text(
            "SELECT ca.* FROM chunk_assets ca "
            "JOIN chunks c ON c.id = ca.chunk_id WHERE c.document_id = :id"
        ),
        {"id": doc_id},
    ).mappings().all()
    assert len(assets) == 1
    # The hotlinked URL, not a local images_dir()-relative path — this is
    # what resolve_asset_url (repositories/assets.py) checks for at read time.
    assert assets[0]["file_path"] == "https://cdn.example.com/img/photo.jpg"


def test_run_article_pipeline_failure_marks_document_and_job_failed(db_session_sync):
    doc_id = uuid4()
    job_id = uuid4()
    _insert_document_and_job(db_session_sync, doc_id, job_id)

    with patch(
        "app.services.article_extraction.fetch_resource",
        side_effect=ArticleExtractionError("Couldn't fetch that page: timed out"),
    ):
        with pytest.raises(ArticleExtractionError):
            run_article_pipeline_sync(
                db_session_sync,
                document_id=doc_id,
                job_id=job_id,
                url="https://example.com/some-article",
            )

    doc = db_session_sync.execute(
        text("SELECT status, error_message FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().one()
    assert doc["status"] == "failed"
    # The reader-facing message survives sanitization verbatim — see
    # pipeline_sync._sanitize_error_for_user's ArticleExtractionError case.
    assert "Couldn't fetch that page" in doc["error_message"]

    job = db_session_sync.execute(
        text("SELECT status FROM ingestion_jobs WHERE id = :id"), {"id": job_id}
    ).mappings().one()
    assert job["status"] == "failed"

    # No dirty partial chunks left behind after a failure.
    remaining = db_session_sync.execute(
        text("SELECT COUNT(*) FROM chunks WHERE document_id = :id"), {"id": doc_id}
    ).scalar_one()
    assert remaining == 0


# ── a URL that turns out to be a PDF ────────────────────────────────────────

def _pdf_resource(url="https://arxiv.org/pdf/1706.03762"):
    # Enough of a PDF for is_pdf; the pipeline itself is mocked below.
    return FetchedResource(
        content=b"%PDF-1.5\nnot a real body, run_pipeline_sync is mocked\n",
        content_type="application/pdf",
        final_url=url,
    )


def test_pdf_url_is_routed_to_the_pdf_pipeline(db_session_sync, tmp_path, monkeypatch):
    """A pasted arXiv PDF link must become a real doc_kind='paper' document
    running through MinerU — not an 'article' fed to a static-HTML extractor,
    which is what produced the 'may require a login or JavaScript' failure.
    """
    import app.extraction.pipeline_sync as ps

    doc_id = uuid4()
    job_id = uuid4()
    _insert_document_and_job(db_session_sync, doc_id, job_id)

    monkeypatch.setattr(ps, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "assets_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "ensure_storage_dirs", lambda: None)

    with patch(
        "app.services.article_extraction.fetch_resource", return_value=_pdf_resource()
    ), patch.object(ps, "run_pipeline_sync") as run_pdf:
        run_article_pipeline_sync(
            db_session_sync,
            document_id=doc_id,
            job_id=job_id,
            url="https://arxiv.org/pdf/1706.03762",
        )

    # The PDF pipeline ran, pointed at the file just written.
    run_pdf.assert_called_once()
    assert run_pdf.call_args.kwargs["pdf_path"] == tmp_path / f"{doc_id}.pdf"

    # Bytes landed where the pipeline reads them AND where /raw serves them.
    assert (tmp_path / f"{doc_id}.pdf").read_bytes().startswith(b"%PDF-")

    doc = db_session_sync.execute(
        text("SELECT * FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().one()
    assert doc["doc_kind"] == "paper"
    assert doc["filename"] == f"{doc_id}.pdf"
    # A human-readable name from the URL, not the placeholder .html one.
    assert doc["original_filename"] == "1706.03762.pdf"
    # Provenance survives: this row knows it came from a link.
    assert doc["source_url"] == "https://example.com/some-article"


def test_html_url_still_takes_the_article_path(db_session_sync):
    """Guard the branch: a normal page must not be dragged into the PDF
    pipeline by the new routing."""
    import app.extraction.pipeline_sync as ps

    doc_id = uuid4()
    job_id = uuid4()
    _insert_document_and_job(db_session_sync, doc_id, job_id)

    fake = ArticleExtraction(
        title="An Ordinary Page",
        markdown="# An Ordinary Page\n\nA paragraph of real prose here.\n",
        asset_map={},
    )
    with patch(
        "app.services.article_extraction.fetch_resource", return_value=_html_resource()
    ), patch(
        "app.services.article_extraction.extract_article_from_html", return_value=fake
    ), patch.object(ps, "run_pipeline_sync") as run_pdf:
        run_article_pipeline_sync(
            db_session_sync,
            document_id=doc_id,
            job_id=job_id,
            url="https://example.com/some-article",
        )

    run_pdf.assert_not_called()
    doc = db_session_sync.execute(
        text("SELECT doc_kind, extractor FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().one()
    assert doc["doc_kind"] == "article"
    assert doc["extractor"] == "trafilatura"


def test_pdf_url_with_book_kind_becomes_doc_kind_book(db_session_sync, tmp_path, monkeypatch):
    """A link pasted through the 'Book' picker, not just 'Article by URL',
    must land as doc_kind='book' when it turns out to be a PDF."""
    import app.extraction.pipeline_sync as ps

    doc_id = uuid4()
    job_id = uuid4()
    _insert_document_and_job(db_session_sync, doc_id, job_id)

    monkeypatch.setattr(ps, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "assets_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "ensure_storage_dirs", lambda: None)

    with patch(
        "app.services.article_extraction.fetch_resource", return_value=_pdf_resource()
    ), patch.object(ps, "run_pipeline_sync") as run_pdf:
        run_article_pipeline_sync(
            db_session_sync,
            document_id=doc_id,
            job_id=job_id,
            url="https://arxiv.org/pdf/1706.03762",
            kind="book",
        )

    run_pdf.assert_called_once()
    doc = db_session_sync.execute(
        text("SELECT doc_kind FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().one()
    assert doc["doc_kind"] == "book"


def test_non_pdf_url_with_paper_kind_still_becomes_an_article(db_session_sync):
    """The PubMed case: a link pasted through 'Research paper' that turns out
    to be an abstract page, not a PDF, must NOT error and must NOT force a
    PDF-shaped doc_kind onto content that was never a PDF — there is no
    PDF-based pipeline to honor the hint with, so it becomes a normal
    article, same as if 'Article by URL' had been used directly."""
    import app.extraction.pipeline_sync as ps

    doc_id = uuid4()
    job_id = uuid4()
    _insert_document_and_job(db_session_sync, doc_id, job_id)

    fake = ArticleExtraction(
        title="A PubMed Abstract",
        markdown="# A PubMed Abstract\n\nSome real prose content for the test.\n",
        asset_map={},
    )
    with patch(
        "app.services.article_extraction.fetch_resource", return_value=_html_resource()
    ), patch(
        "app.services.article_extraction.extract_article_from_html", return_value=fake
    ), patch.object(ps, "run_pipeline_sync") as run_pdf:
        run_article_pipeline_sync(
            db_session_sync,
            document_id=doc_id,
            job_id=job_id,
            url="https://pubmed.ncbi.nlm.nih.gov/12345/",
            kind="paper",
        )

    run_pdf.assert_not_called()
    doc = db_session_sync.execute(
        text("SELECT doc_kind, extractor FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().one()
    assert doc["doc_kind"] == "article"
    assert doc["extractor"] == "trafilatura"
