"""Tests for aird/cli/transfer_http.py parallel range transfers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from aird.cli.transfer_http import (
    _clone_session,
    _put_chunk,
    _range_session,
    download_file_ranged,
    upload_file_ranged,
)


def test_clone_session():
    src = requests.Session()
    src.cookies.set("sid", "abc")
    src.headers["Authorization"] = "Bearer tok"
    clone = _clone_session(src)
    assert clone.cookies.get("sid") == "abc"
    assert clone.headers["Authorization"] == "Bearer tok"
    assert clone is not src


def test_range_session_success():
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=201, json=lambda: {"upload_id": "up-1"})
    uid = _range_session(http, "https://x.test", {}, "docs", "big.bin", 1000)
    assert uid == "up-1"


def test_range_session_failure():
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=500, text="fail")
    with pytest.raises(RuntimeError, match="Range session failed"):
        _range_session(http, "https://x.test", {}, "", "f", 1)


def test_put_chunk_complete():
    http = MagicMock()
    http.put.return_value = MagicMock(status_code=201)
    assert _put_chunk(http, "https://x.test", {}, "up-1", b"x", 0, 0, 1) is True


def test_put_chunk_error():
    http = MagicMock()
    http.put.return_value = MagicMock(status_code=500, text="bad")
    with pytest.raises(RuntimeError, match="Chunk upload failed"):
        _put_chunk(http, "https://x.test", {}, "up-1", b"x", 0, 0, 1)


def test_upload_file_ranged(tmp_path):
    local = tmp_path / "data.bin"
    local.write_bytes(b"a" * 32)
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=201, json=lambda: {"upload_id": "up-1"})
    http.put.return_value = MagicMock(status_code=201)

    with patch("aird.cli.transfer_http._clone_session", side_effect=lambda s: s):
        upload_file_ranged(
            http,
            "https://x.test",
            {"X-XSRFToken": "xsrf"},
            local,
            chunk_size=16,
            workers=1,
        )
    assert http.put.called


def test_download_file_ranged(tmp_path):
    http = MagicMock()
    http.head.return_value = MagicMock(status_code=200, headers={"Content-Length": "32"})
    http.get.return_value = MagicMock(status_code=206, content=b"a" * 32)
    dest = tmp_path / "out.bin"

    with patch("aird.cli.transfer_http._clone_session", side_effect=lambda s: s):
        download_file_ranged(
            http,
            "https://x.test",
            "docs/file.bin",
            dest,
            chunk_size=32,
            workers=1,
        )
    assert dest.read_bytes() == b"a" * 32


def test_download_file_ranged_head_failure():
    http = MagicMock()
    http.head.return_value = MagicMock(status_code=404)
    with pytest.raises(RuntimeError, match="HEAD failed"):
        download_file_ranged(http, "https://x.test", "missing", Path("/tmp/x"))
