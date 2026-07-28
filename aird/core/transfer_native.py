"""Optional Rust extension (aird_transfer) for upload fd pumps.

Build (from repo root):
    pip install maturin
    cd native/aird_transfer && maturin develop --release

When the extension is not installed, all helpers fall back to pure Python.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

_NATIVE_MIN_BYTES = 4 * 1024 * 1024

_transfer_mod: Any = None
_import_attempted = False


def _load_extension() -> Any | None:
    global _transfer_mod, _import_attempted
    if _import_attempted:
        return _transfer_mod
    _import_attempted = True
    try:
        import aird_transfer as mod  # type: ignore[import-not-found]

        _transfer_mod = mod
        logger.info("aird_transfer native extension loaded")
    except ImportError:
        logger.debug("aird_transfer not installed; using Python upload fallbacks")
        _transfer_mod = None
    return _transfer_mod


def native_available() -> bool:
    return _load_extension() is not None


def socket_pump_supported() -> bool:
    """Direct socket→disk recv bypass (Linux only; breaks Tornado on Windows)."""
    return sys.platform.startswith("linux") and native_available()


def write_fd(fd: int, data: bytes) -> int:
    """Write bytes to an open file descriptor (native or os.write fallback)."""
    if sys.platform == "win32":
        # CRT fds from os.open/mkstemp; Rust uses libc::write on Windows too after rebuild.
        try:
            mod = _load_extension()
            if mod is not None:
                return int(mod.write_fd(fd, data))
        except OSError:
            pass
        return os.write(fd, data)
    mod = _load_extension()
    if mod is not None:
        return int(mod.write_fd(fd, data))
    return os.write(fd, data)


def recv_to_fd(
    sock_fd: int,
    file_fd: int,
    max_bytes: int,
    cancel: Any | None = None,
) -> int:
    mod = _load_extension()
    if mod is None:
        raise RuntimeError("aird_transfer extension is not installed")
    return int(mod.recv_to_fd(sock_fd, file_fd, max_bytes, cancel))


def new_cancel_flag() -> Any | None:
    mod = _load_extension()
    if mod is None:
        return None
    return mod.CancelFlag()


def pause_request_stream(handler) -> int | None:
    """Stop Tornado from reading the request body so we can recv() on the socket fd."""
    conn = getattr(handler.request, "connection", None)
    if conn is None:
        return None
    stream = getattr(conn, "stream", None)
    if stream is None:
        return None
    sock = getattr(stream, "socket", None)
    if sock is None:
        return None
    fd = sock.fileno()
    io_loop = getattr(stream, "io_loop", None)
    if io_loop is None:
        return None
    try:
        io_loop.remove_handler(fd)
    except (KeyError, ValueError, OSError):
        return None
    return fd


class NativeSocketUploadPump:
    """Background thread: socket fd → file fd for the HTTP request body."""

    def __init__(
        self,
        sock_fd: int,
        file_fd: int,
        nbytes: int,
        cancel: Any,
    ) -> None:
        self._sock_fd = sock_fd
        self._file_fd = file_fd
        self._nbytes = nbytes
        self._cancel = cancel
        self.bytes_written = 0
        self.error: BaseException | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="aird-native-upload", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            self.bytes_written = recv_to_fd(
                self._sock_fd,
                self._file_fd,
                self._nbytes,
                self._cancel,
            )
        except Exception as exc:
            self.error = exc
            logger.warning("Native upload pump failed: %s", exc)
        finally:
            self._done.set()

    def wait(self, timeout: float = 600.0) -> int:
        self._done.wait(timeout=timeout)
        if self._thread.is_alive():
            self._thread.join(timeout=min(30.0, timeout))
        if self.error is not None:
            raise self.error
        return self.bytes_written

    def abort(self) -> None:
        if self._cancel is not None and hasattr(self._cancel, "cancel"):
            self._cancel.cancel()
        self._done.wait(timeout=5.0)


def try_start_native_socket_upload(
    handler,
    file_fd: int,
    nbytes: int,
) -> bool:
    """Pause Tornado stream read and pump body bytes natively when possible."""
    if nbytes < _NATIVE_MIN_BYTES or nbytes <= 0:
        return False
    if not socket_pump_supported():
        return False
    cancel = new_cancel_flag()
    if cancel is None:
        return False
    sock_fd = pause_request_stream(handler)
    if sock_fd is None:
        return False
    pump = NativeSocketUploadPump(sock_fd, file_fd, nbytes, cancel)
    pump.start()
    handler._native_pump = pump
    handler._native_cancel = cancel
    logger.debug(
        "Native upload pump started (%d bytes, sock=%d file=%d)",
        nbytes,
        sock_fd,
        file_fd,
    )
    return True
