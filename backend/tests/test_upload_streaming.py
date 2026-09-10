from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.documents import _UPLOAD_CHUNK_BYTES, _stream_pdf_upload


class _Upload:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.read_sizes: list[int] = []
        self.closed = False

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.chunks.pop(0) if self.chunks else b""

    async def close(self) -> None:
        self.closed = True


async def test_stream_upload_writes_a_valid_pdf_in_bounded_reads(tmp_path: Path):
    upload = _Upload([b"%PDF-1.7\n", b"x" * 20])
    destination = tmp_path / "paper.pdf"

    size = await _stream_pdf_upload(upload, destination, max_bytes=100)

    assert size == len(b"%PDF-1.7\n") + 20
    assert destination.read_bytes() == b"%PDF-1.7\n" + b"x" * 20
    assert upload.closed is True
    assert upload.read_sizes == [_UPLOAD_CHUNK_BYTES] * 3


async def test_stream_upload_removes_partial_file_when_limit_is_exceeded(tmp_path: Path):
    upload = _Upload([b"%PDF-1.7\n", b"x" * 100])
    destination = tmp_path / "too-large.pdf"

    with pytest.raises(HTTPException, match="File too large"):
        await _stream_pdf_upload(upload, destination, max_bytes=100)

    assert not destination.exists()
    assert upload.closed is True
