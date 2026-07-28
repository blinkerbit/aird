"""Tests for aird.core.fast_upload.FastUploadWriter."""

from __future__ import annotations

import os
import tempfile

from aird.core.fast_upload import FastUploadWriter


def test_fast_upload_writer_roundtrip(tmp_path):
    path = tmp_path / "out.bin"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    writer = FastUploadWriter(fd, coalesce_bytes=1024, queue_maxitems=8)
    payload = b"abcdefgh" * 400  # 3200 bytes → multiple coalesce flushes
    for i in range(0, len(payload), 100):
        writer.feed(payload[i : i + 100])
    writer.finish(timeout=10)
    assert writer.error is None
    assert path.read_bytes() == payload


def test_fast_upload_writer_abort_skips_pending(tmp_path):
    path = tmp_path / "out.bin"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    writer = FastUploadWriter(fd, coalesce_bytes=1024, queue_maxitems=8)
    writer.feed(b"x" * 1024)
    writer.feed(b"y" * 1024)
    writer.abort()
    written = path.read_bytes()
    assert len(written) < 2048
