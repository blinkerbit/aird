"""Tests for aird/cli/transfer_http.py stream transfers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aird.cli.transfer_http import download_file, upload_file


def test_upload_file_success(tmp_path):
    local = tmp_path / "data.bin"
    local.write_bytes(b"hello")
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=200, text="ok")

    upload_file(http, "https://x.test", {"X-XSRFToken": "xsrf"}, local, "docs")
    assert http.post.called


def test_upload_file_failure(tmp_path):
    local = tmp_path / "data.bin"
    local.write_bytes(b"x")
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=500, text="fail")
    with pytest.raises(RuntimeError, match="Upload failed"):
        upload_file(http, "https://x.test", {}, local)


def test_download_file_success(tmp_path):
    http = MagicMock()
    response = MagicMock(status_code=200, headers={"Content-Length": "5"})
    response.iter_content.return_value = [b"hello"]
    response.__enter__ = lambda s: s
    response.__exit__ = lambda *args: None
    http.get.return_value = response
    dest = tmp_path / "out.bin"

    download_file(http, "https://x.test", "docs/file.bin", dest)
    assert dest.read_bytes() == b"hello"


def test_download_file_failure(tmp_path):
    http = MagicMock()
    response = MagicMock(status_code=404)
    response.__enter__ = lambda s: s
    response.__exit__ = lambda *args: None
    http.get.return_value = response
    with pytest.raises(RuntimeError, match="Download failed"):
        download_file(http, "https://x.test", "missing", tmp_path / "x")
