"""Tests for zero-copy file send helpers."""

from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from aird.core.file_send import sendfile_available, sendfile_to_socket, _sendfile_sync


def test_sendfile_available_on_linux():
    with patch.object(sys, "platform", "linux"):
        with patch.object(os, "sendfile", create=True):
            assert sendfile_available() is True
    with patch.object(sys, "platform", "darwin"):
        assert sendfile_available() is False


@pytest.mark.asyncio
async def test_sendfile_unsupported_platform():
    with patch("aird.core.file_send.sendfile_available", return_value=False):
        assert await sendfile_to_socket(MagicMock(), "/tmp/x") is False


@pytest.mark.asyncio
async def test_sendfile_success(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    sock = MagicMock()
    sock.fileno.return_value = 1
    with patch("aird.core.file_send.sendfile_available", return_value=True), patch(
        "aird.core.file_send._sendfile_sync", return_value=5
    ):
        assert await sendfile_to_socket(sock, str(src)) is True


def test_sendfile_sync_partial():
    out_fd, in_fd = 1, 2
    with patch("os.sendfile", side_effect=[3, 0], create=True) as sf:
        sent = _sendfile_sync(out_fd, in_fd, 0, 10)
    assert sent == 3
    assert sf.call_count == 2
