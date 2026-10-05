"""Generated article covers share PDF serving, cache, and deletion paths."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.responses import FileResponse


@pytest.mark.asyncio
async def test_article_without_cover_returns_204_and_no_store(monkeypatch):
    from app.api.v1.endpoints import documents

    document_id = uuid4()
    monkeypatch.setattr(
        documents.doc_service,
        "get_document",
        AsyncMock(return_value={"id": document_id, "filename": "article.html", "doc_kind": "article"}),
    )
    monkeypatch.setattr(documents.cover_service, "render_cover", lambda *_: None)

    response = await documents.get_paper_cover(
        document_id, db=object(), current_user={"id": uuid4()}
    )

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_article_cover_is_served_as_jpeg(monkeypatch, tmp_path):
    from app.api.v1.endpoints import documents

    document_id = uuid4()
    cover = tmp_path / "article.jpg"
    cover.write_bytes(b"\xff\xd8generated-jpeg")
    monkeypatch.setattr(
        documents.doc_service,
        "get_document",
        AsyncMock(return_value={"id": document_id, "filename": "article.html", "doc_kind": "article"}),
    )
    monkeypatch.setattr(documents.cover_service, "render_cover", lambda *_: cover)

    response = await documents.get_paper_cover(
        document_id, db=object(), current_user={"id": uuid4()}
    )

    assert isinstance(response, FileResponse)
    assert response.status_code == 200
    assert response.media_type == "image/jpeg"


@pytest.mark.asyncio
async def test_article_cover_version_is_in_document_list(monkeypatch, tmp_path):
    from app.api.v1.endpoints import documents

    document_id = uuid4()
    cover = tmp_path / f"{document_id}.jpg"
    cover.write_bytes(b"\xff\xd8generated-jpeg")
    row = {
        "id": document_id,
        "filename": "article.html",
        "original_filename": "An article",
        "status": "complete",
        "created_at": datetime.now(timezone.utc),
        "doc_kind": "article",
    }
    monkeypatch.setattr(documents.doc_service, "list_documents", AsyncMock(return_value=[row]))
    monkeypatch.setattr(documents.doc_service, "count_documents", AsyncMock(return_value=1))
    monkeypatch.setattr(documents.cover_service, "cover_path", lambda _id: cover)

    response = await documents.list_papers(db=object(), current_user={"id": uuid4()})

    assert response.documents[0].cover_version == int(cover.stat().st_mtime)


@pytest.mark.asyncio
async def test_deleting_an_article_runs_shared_cover_cleanup(monkeypatch, tmp_path):
    from app.api.v1.endpoints import documents

    document_id = uuid4()

    class Database:
        async def commit(self):
            return None

    monkeypatch.setattr(
        documents.doc_service,
        "delete_document",
        AsyncMock(return_value={"filename": "article.html", "doc_kind": "article"}),
    )
    deleted_covers = []
    monkeypatch.setattr(documents.cover_service, "delete_cover", lambda value: deleted_covers.append(value))
    for path_name in ("documents_dir", "assets_dir", "extracted_dir", "images_dir"):
        monkeypatch.setattr(documents, path_name, lambda: tmp_path)

    await documents.delete_paper(
        document_id, db=Database(), current_user={"id": uuid4()}
    )

    assert deleted_covers == [document_id]
