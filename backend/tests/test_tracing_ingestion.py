import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core import tracing

EXPECTED = {
    "app.extraction.pipeline_sync": {
        "run_pipeline_sync": ("ingest.pdf", "CHAIN"), "run_article_pipeline_sync": ("ingest.article", "CHAIN"),
        "resolve_extractor": ("extract", "CHAIN"), "_get_arabic_classification": ("classify", "CHAIN"),
        "_finish_ingestion": ("persist", "CHAIN"),
    },
    "app.extraction.mineru_client": {"extract_pdf_sync": ("extract.mineru", "CHAIN"), "find_images": ("assets.find", "CHAIN")},
    "app.extraction.vlm_client": {"extract_via_vlm": ("extract.vlm", "CHAIN")},
    "app.extraction.chunker": {
        "create_chunks_from_content_list": ("chunk", "CHAIN"), "create_chunks_from_markdown": ("chunk.markdown", "CHAIN"),
        "crop_code_blocks": ("code_crops", "CHAIN"),
    },
    "app.extraction.glyph_repair": {"repair_chunks": ("glyph_repair", "CHAIN")},
    "app.extraction.heading_repair": {"repair_headings": ("heading_repair", "CHAIN")},
    "app.extraction.arabic_ocr": {"extract_arabic_document": ("extract.arabic", "CHAIN")},
    "app.extraction.arabic_classifier": {"classify_document": ("classify.document", "CHAIN"),
                                         "call_local_router": ("classify.vision", "LLM")},
    "app.services.article_extraction": {"extract_article": ("article.fetch", "TOOL"),
                                        "extract_article_from_html": ("article.extract", "CHAIN")},
    "app.embeddings.service_sync": {"embed_document_chunks_sync": ("embed", "EMBEDDING")},
    "app.summarization.section_summarizer_sync": {"generate_and_store_section_summaries_sync": ("summaries", "CHAIN")},
    "app.summarization.figure_describer_sync": {"generate_figure_descriptions_sync": ("figure_descriptions", "CHAIN")},
}


@pytest.mark.parametrize("module,functions", EXPECTED.items())
def test_ingestion_functions_are_traced_blocks(module, functions):
    loaded = importlib.import_module(module)
    for name, expected in functions.items():
        assert getattr(getattr(loaded, name), "__traced__", None) == expected, f"{module}.{name}"


def test_provider_methods_are_traced():
    from app.extraction.arabic_fallback import GemmaArabicFallback
    from app.extraction.gemini_ocr_client import GeminiOcrClient

    assert GeminiOcrClient.generate_batch.__traced__ == ("llm.gemini_ocr", "LLM")
    assert GemmaArabicFallback.generate_page.__traced__ == ("llm.gemma_ocr", "LLM")


def test_asset_moves_are_events_on_the_enclosing_block(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.extraction import assets

    exporter = tracing.use_in_memory_exporter()
    try:
        # assets.py has no `settings` name of its own (it calls images_dir(),
        # which reads app.core.paths' own imported settings singleton) — patch
        # the shared settings object directly rather than a nonexistent
        # `assets.settings` attribute.
        monkeypatch.setattr(settings, "storage_root", str(tmp_path / "storage"))
        image = tmp_path / "fig.jpg"
        image.write_bytes(b"jpeg")
        with tracing.span("ingest.pdf"):
            assets.move_asset_to_storage(image, document_id="5ffce224-80ab-5d5a-b53f-462e57021237")
        s = exporter.get_finished_spans()[0]
        assert [e.name for e in s.events] == ["asset_moved"]
        assert s.events[0].attributes["source"] == "fig.jpg"
    finally:
        tracing.reset_for_tests()


def _run_pipeline_success(db_session_sync, tmp_path, monkeypatch):
    """Local copy of tests.test_ingestion_pipeline.test_run_pipeline_success's
    setup (there is no tests/__init__.py, so that module cannot be imported
    by dotted path here) — runs the same success-path pipeline so the
    resulting trace tree can be inspected.
    """
    from app.core.config import settings
    monkeypatch.setattr(settings, "arabic_ocr_enabled", False)
    doc_id = uuid4()
    job_id = uuid4()
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_text("fake pdf content")

    db_session_sync.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'test.pdf', 'original.pdf', 'queued')"
        ),
        {"id": doc_id},
    )
    db_session_sync.execute(
        text(
            "INSERT INTO ingestion_jobs (id, document_id, status) "
            "VALUES (:id, :doc_id, 'queued')"
        ),
        {"id": job_id, "doc_id": doc_id},
    )
    db_session_sync.commit()

    fake_extracted_dir = tmp_path / "extracted"
    fake_extracted_dir.mkdir()

    fake_md = fake_extracted_dir / "output.md"
    fake_md.write_text(
        "# Introduction\nThis is a test research paper.\n\n"
        "## Method\nHere is a formula:\n$$\nx = y + 1\n$$\n\n"
        "Here is a figure:\n![A diagram](images/fig1.png)\n\n"
        "And a table:\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    )

    img_path = fake_extracted_dir / "images" / "fig1.png"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img_path.write_text("fake image data")

    monkeypatch.setattr("app.core.config.settings.generate_figure_descriptions", True)

    from app.extraction.pipeline_sync import run_pipeline_sync

    with patch("app.extraction.pipeline_sync.extract_pdf_sync", return_value=(fake_extracted_dir, "mineru")), \
         patch("app.extraction.pipeline_sync.find_markdown_output", return_value=fake_md), \
         patch("app.extraction.pipeline_sync.find_images", return_value=[img_path]), \
         patch("app.extraction.pipeline_sync.move_asset_to_storage", return_value={
             "asset_type": "image",
             "file_path": f"{doc_id}/moved_fig1.png",
             "mime_type": "image/png",
             "original_name": "fig1.png",
         }), \
         patch("app.workers.tasks.embed_document.delay") as mock_embed_delay, \
         patch("app.workers.tasks.generate_figure_descriptions.delay") as mock_figure_delay:

        run_pipeline_sync(
            db_session_sync,
            document_id=doc_id,
            job_id=job_id,
            pdf_path=pdf_path,
        )


def test_pipeline_run_produces_the_ingestion_tree(db_session_sync, tmp_path, monkeypatch):
    """Reuses the existing success-path fixture shape from test_ingestion_pipeline."""
    exporter = tracing.use_in_memory_exporter()
    try:
        _run_pipeline_success(db_session_sync, tmp_path, monkeypatch)
        names = [s.name for s in exporter.get_finished_spans()]
        assert "ingest.pdf" in names and "persist" in names
        root = [s for s in exporter.get_finished_spans() if s.name == "ingest.pdf"][0]
        assert all(s.context.trace_id == root.context.trace_id for s in exporter.get_finished_spans())
    finally:
        tracing.reset_for_tests()


def test_article_output_traces_images_from_asset_map():
    """Test that article_output records article image count and names from asset_map,
    renders it as {count: N} in the summary, and leaves the original unchanged."""
    from app.services.article_extraction import ArticleExtraction
    from app.extraction.tracing_hooks import article_output

    exporter = tracing.use_in_memory_exporter()
    try:
        # Create an ArticleExtraction with 2 images in asset_map
        original_asset_map = {
            "fig1.png": "https://example.com/fig1.png",
            "fig2.jpg": "https://example.com/fig2.jpg",
        }
        article = ArticleExtraction(
            title="Test Article",
            markdown="# Test\n![Fig1](fig1.png)\n![Fig2](fig2.jpg)",
            asset_map=original_asset_map.copy(),
        )

        # Call article_output inside a span
        with tracing.span("article.fetch"):
            summary = article_output(article)

        # Get the finished span
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "article.fetch"

        # Check that the span recorded the image count and names
        assert span.attributes.get("article.images") == 2
        image_names = span.attributes.get("article.image_names")
        assert image_names is not None
        # image_names is a stringified version of the list, should contain both filenames
        assert "fig1.png" in image_names
        assert "fig2.jpg" in image_names

        # Check that the returned summary has asset_map rendered as {count: 2}
        assert summary["asset_map"] == {"count": 2}
        assert summary["title"] == "Test Article"
        assert summary["markdown"] == "# Test\n![Fig1](fig1.png)\n![Fig2](fig2.jpg)"

        # Check that the original ArticleExtraction's asset_map is unchanged
        assert article.asset_map == original_asset_map
    finally:
        tracing.reset_for_tests()
