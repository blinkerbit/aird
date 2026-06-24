"""Additional mmap handler unit tests."""

from __future__ import annotations

import os
import tempfile

import pytest

from aird.core.mmap_handler import MMapFileHandler, _SyncChunkReader, _read_chunks_sync


def test_read_chunks_sync():
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"0123456789")
        path = fh.name
    try:
        chunks = _read_chunks_sync(path, 2, 5, 10, 3)
        assert b"".join(chunks) == b"2345"
    finally:
        os.unlink(path)


def test_sync_chunk_reader_mmap_and_plain():
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"abcdefghij")
        path = fh.name
    try:
        for use_mmap in (False, True):
            reader = _SyncChunkReader(path, 0, 4, 10, 3, use_mmap=use_mmap)
            reader.open()
            parts = []
            while True:
                chunk = reader.read_next()
                if chunk is None:
                    break
                parts.append(chunk)
            reader.close()
            assert b"".join(parts) == b"abcde"
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_serve_file_chunk_empty_tail():
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"abc")
        path = fh.name
    try:
        chunks = [c async for c in MMapFileHandler.serve_file_chunk(path, start=1, end=1)]
        assert b"".join(chunks) == b"b"
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_serve_file_chunk_oserror_fallback():
    with patch("asyncio.to_thread", side_effect=[OSError("bad"), 3]):
        chunks = []
        async for chunk in MMapFileHandler.serve_file_chunk("/nonexistent"):
            chunks.append(chunk)
        assert chunks == []


def test_find_line_offsets_large_file():
    from aird.constants import MMAP_MIN_SIZE

    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"0" * (MMAP_MIN_SIZE + 10))
        path = fh.name
    try:
        offsets = MMapFileHandler.find_line_offsets(path, max_lines=1)
        assert offsets == [0]
    finally:
        os.unlink(path)


def test_search_in_file():
    with tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8") as fh:
        fh.write("hello world\nneedle here\n")
        path = fh.name
    try:
        hits = MMapFileHandler.search_in_file(path, "needle")
        assert len(hits) >= 1
    finally:
        os.unlink(path)
