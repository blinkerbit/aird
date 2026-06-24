"""Tests for compression negotiation."""

import asyncio
import gzip
import tempfile
from pathlib import Path

import aird.constants as constants
from aird.core.compression import (
    codecs_available,
    compress_file,
    negotiate_encoding,
    should_compress,
)


def test_negotiate_encoding_prefers_zstd_on_gil_builds():
    enc = negotiate_encoding("gzip, deflate, zstd")
    avail = codecs_available()
    if avail["zstd"]:
        assert enc == "zstd"
    else:
        assert enc == "gzip"


def test_negotiate_gzip_only():
    assert negotiate_encoding("gzip") == "gzip"


def test_should_compress_text():
    assert should_compress(
        path="/tmp/log.txt",
        mime_type="text/plain",
        file_size=4096,
        has_range=False,
        remote_ip="8.8.8.8",
        compression_enabled=True,
        mode="always",
    )


def test_should_not_compress_range():
    assert not should_compress(
        path="/tmp/log.txt",
        mime_type="text/plain",
        file_size=4096,
        has_range=True,
        remote_ip="8.8.8.8",
        compression_enabled=True,
        mode="always",
    )


def test_compression_algorithms_default_gzip_only():
    assert "gzip" in constants.COMPRESSION_CONFIG["algorithms"]
    assert "br" not in constants.COMPRESSION_CONFIG["algorithms"]


def test_compress_file_gzip():
    with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".txt") as f:
        f.write(b"hello world " * 100)
        path = f.name
    try:
        out = asyncio.run(compress_file(path, "gzip", 6))
        assert out[:2] == b"\x1f\x8b"
        assert gzip.decompress(out).startswith(b"hello")
    finally:
        Path(path).unlink(missing_ok=True)
