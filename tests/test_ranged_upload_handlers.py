"""Tests for resumable ranged HTTP uploads."""

import os

import pytest

from aird.handlers.ranged_upload_handlers import (
    _ensure_upload_file_size_sync,
    _write_range_sync,
)


def test_write_range_grows_file_without_upfront_truncate(tmp_path):
    path = tmp_path / "upload.bin"
    path.write_bytes(b"")
    _write_range_sync(str(path), 1_000_000, b"abc")
    assert os.path.getsize(path) == 1_000_003


def test_write_range_sparse_middle_extends_file(tmp_path):
    path = tmp_path / "sparse.bin"
    path.write_bytes(b"")
    _write_range_sync(str(path), 0, b"head")
    _write_range_sync(str(path), 10, b"tail")
    assert os.path.getsize(path) == 14
    with open(path, "rb") as fh:
        assert fh.read() == b"head\x00\x00\x00\x00\x00\x00tail"


def test_ensure_upload_file_size_truncates_only_at_finalize(tmp_path):
    path = tmp_path / "final.bin"
    path.write_bytes(b"x" * 20)
    _ensure_upload_file_size_sync(str(path), 10)
    assert os.path.getsize(path) == 10


def test_ensure_upload_file_size_extends_short_file(tmp_path):
    path = tmp_path / "short.bin"
    path.write_bytes(b"abc")
    _ensure_upload_file_size_sync(str(path), 8)
    assert os.path.getsize(path) == 8
