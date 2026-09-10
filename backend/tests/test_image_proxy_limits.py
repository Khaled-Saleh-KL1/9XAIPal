from pathlib import Path

import httpx
import pytest

from app.services import image_service


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


async def test_image_proxy_stops_reading_once_stream_exceeds_limit(monkeypatch, tmp_path: Path):
    stream = _Chunks([b"x" * (image_service.MAX_IMAGE_BYTES - 1), b"xx"])
    response = httpx.Response(
        200,
        headers={"content-type": "image/png"},
        stream=stream,
        request=httpx.Request("GET", "https://public.example/image.png"),
    )

    async def _safe_send(*_args, **_kwargs):
        return response

    async def _public_address(_url: str) -> bool:
        return False

    monkeypatch.setattr(image_service, "safe_send_async", _safe_send)
    monkeypatch.setattr(image_service, "resolves_to_private_address", _public_address)
    monkeypatch.setattr(image_service, "_proxy_cache_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="Image too large"):
        await image_service.fetch_image_via_proxy("https://public.example/image.png")

    assert stream.closed is True


async def test_image_proxy_rejects_an_oversized_content_length(monkeypatch, tmp_path: Path):
    stream = _Chunks([])
    response = httpx.Response(
        200,
        headers={
            "content-type": "image/png",
            "content-length": str(image_service.MAX_IMAGE_BYTES + 1),
        },
        stream=stream,
        request=httpx.Request("GET", "https://public.example/image.png"),
    )

    async def _safe_send(*_args, **_kwargs):
        return response

    async def _public_address(_url: str) -> bool:
        return False

    monkeypatch.setattr(image_service, "safe_send_async", _safe_send)
    monkeypatch.setattr(image_service, "resolves_to_private_address", _public_address)
    monkeypatch.setattr(image_service, "_proxy_cache_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="Image too large"):
        await image_service.fetch_image_via_proxy("https://public.example/image.png")

    assert stream.closed is True
