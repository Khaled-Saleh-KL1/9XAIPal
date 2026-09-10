"""Unit tests for the authenticated media URL and filesystem boundaries."""

from uuid import uuid4

import pytest

from app.api.v1.endpoints.chunks import _local_asset_file
from app.chat.agent_tools import attach_asset_urls
from app.api.v1.endpoints import chunks, media
from app.core.config import settings
from app.database.repositories.assets import resolve_asset_url
from app.main import app


def test_local_asset_url_names_its_parent_document():
    document_id = uuid4()
    assert resolve_asset_url(document_id, "figures/plot 1.png") == (
        f"/api/v1/papers/{document_id}/assets/figures/plot%201.png"
    )
    assert resolve_asset_url(document_id, "https://example.test/plot.png") == (
        "https://example.test/plot.png"
    )


def test_local_asset_file_cannot_escape_images_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    image = tmp_path / "images" / "doc" / "figure.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    assert _local_asset_file("doc/figure.png") == image.resolve()
    assert _local_asset_file("../secrets.txt") is None
    assert _local_asset_file("/etc/passwd") is None
    assert _local_asset_file("https://example.test/figure.png") is None


def test_private_storage_is_not_mounted_as_static_files():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/static/images" not in paths
    assert "/static/extracted" not in paths
    assert "/static/assets" not in paths
    media_paths = {getattr(route, "path", "") for route in media.router.routes}
    chunk_paths = {getattr(route, "path", "") for route in chunks.router.routes}
    assert "/research/{conversation_id}/{filename}" in media_paths
    assert "/{paper_id}/assets/{asset_path:path}" in chunk_paths


@pytest.mark.asyncio
async def test_attach_asset_urls_uses_the_assets_parent_document(monkeypatch):
    document_id = uuid4()
    chunk_id = uuid4()

    async def fake_assets(_session, _chunk_ids):
        return [{
            "chunk_id": chunk_id,
            "document_id": document_id,
            "asset_type": "image",
            "file_path": "figures/figure.png",
        }]

    from app.chat import agent_tools

    monkeypatch.setattr(agent_tools.asset_repo, "get_assets_for_chunks", fake_assets)
    blocks = [{"id": chunk_id, "sequence_id": 1, "chunk_type": "figure"}]
    await attach_asset_urls(object(), blocks)

    assert blocks[0]["image_url"] == (
        f"/api/v1/papers/{document_id}/assets/figures/figure.png"
    )
