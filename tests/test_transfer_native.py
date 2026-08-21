"""Tests for transfer_native (Rust extension optional)."""

from __future__ import annotations

import os
import tempfile

import pytest

from aird.core.transfer_native import (
    native_available,
    write_fd,
)


def test_write_fd_fallback():
  """write_fd works without the Rust extension (os.write fallback)."""
  with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "out.bin")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    try:
      n = write_fd(fd, b"hello-native")
      assert n == len(b"hello-native")
    finally:
      os.close(fd)
    with open(path, "rb") as f:
      assert f.read() == b"hello-native"


def test_socket_pump_tracks_native_extension():
    from aird.core.transfer_native import native_available, socket_pump_supported

    assert socket_pump_supported() is native_available()


def test_native_available_without_build():
    """Import does not crash when aird_transfer is not installed."""
    assert native_available() in (True, False)


@pytest.mark.skipif(not native_available(), reason="aird_transfer not built")
@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="recv_to_fd roundtrip uses POSIX pipe fds",
)
def test_native_recv_to_fd_roundtrip():
  import aird_transfer

  with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "out.bin")
    file_fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    cancel = aird_transfer.CancelFlag()
    # Pipe: write on one end, recv on the other.
    r_fd, w_fd = os.pipe()
    try:
      payload = b"x" * 8192
      os.write(w_fd, payload)
      os.close(w_fd)
      w_fd = -1
      got = aird_transfer.recv_to_fd(r_fd, file_fd, len(payload), cancel)
      assert got == len(payload)
    finally:
      if w_fd >= 0:
        os.close(w_fd)
      os.close(r_fd)
      os.close(file_fd)
    with open(path, "rb") as f:
      assert f.read() == payload
