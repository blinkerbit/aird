"""Optional Rust extension (aird_transfer) for upload fd pumps.

Build (from repo root):
    pip install maturin
    cd native/aird_transfer && maturin develop --release

When the extension is not installed, all helpers fall back to pure Python.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import socket
import ssl
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

_NATIVE_MIN_BYTES = 1024 * 1024

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
    """True when native recv_to_fd is available (detach upload path)."""
    if sys.platform == "win32":
        return False
    return native_available()


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


def set_socket_blocking(sock: socket.socket, *, blocking: bool = True) -> None:
    """Tornado leaves sockets non-blocking; native recv needs blocking mode."""
    sock.setblocking(blocking)


def tune_transfer_stream(stream: Any, *, buf_bytes: int = 16 * 1024 * 1024) -> None:
    """Enlarge per-connection TCP buffers (listen-socket tuning does not inherit)."""
    sock = getattr(stream, "socket", None)
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buf_bytes)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, buf_bytes)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


def is_ssl_stream(stream: Any) -> bool:
    sock = getattr(stream, "socket", None)
    return isinstance(sock, ssl.SSLSocket)


async def recv_ssl_stream_to_fd(
    stream: Any,
    file_fd: int,
    max_bytes: int,
    cancel: Any | None = None,
    *,
    chunk_size: int = 8 * 1024 * 1024,
) -> int:
    """Read decrypted bytes from a detached SSL IOStream into *file_fd*."""
    remaining = int(max_bytes)
    total = 0
    while remaining > 0:
        if cancel is not None and getattr(cancel, "is_cancelled", lambda: False)():
            break
        chunk = await stream.read_bytes(min(remaining, chunk_size), partial=True)
        if not chunk:
            break
        await asyncio.to_thread(write_fd, file_fd, chunk)
        n = len(chunk)
        total += n
        remaining -= n
    return total


def pwrite_fd(fd: int, data: bytes, offset: int = 0) -> int:
    """Write bytes at offset (native pwrite or Python fallback)."""
    if not data:
        return 0
    mod = _load_extension()
    if mod is not None and hasattr(mod, "pwrite_fd"):
        return int(mod.pwrite_fd(fd, data, int(offset)))
    pwrite = getattr(os, "pwrite", None)
    if pwrite is not None:
        view = memoryview(data)
        written = 0
        while written < len(view):
            n = pwrite(fd, view[written:], int(offset) + written)
            if n <= 0:
                raise OSError("Short pwrite while storing upload range")
            written += n
        return written
    with os.fdopen(os.dup(fd), "r+b", closefd=True) as fh:
        fh.seek(offset)
        return fh.write(data)


async def pump_detached_stream_to_fd(
    stream: Any,
    file_fd: int,
    max_bytes: int,
    cancel: Any | None = None,
    *,
    file_offset: int = 0,
    chunk_size: int = 50 * 1024 * 1024,
) -> int:
    """Read a detached Tornado IOStream body into *file_fd* via Rust writes."""
    remaining = int(max_bytes)
    total = 0
    pos = int(file_offset)
    while remaining > 0:
        if cancel is not None and getattr(cancel, "is_cancelled", lambda: False)():
            break
        chunk = await stream.read_bytes(min(remaining, chunk_size), partial=True)
        if not chunk:
            break
        if pos:
            await asyncio.to_thread(pwrite_fd, file_fd, chunk, pos)
        else:
            await asyncio.to_thread(write_fd, file_fd, chunk)
        n = len(chunk)
        total += n
        remaining -= n
        pos += n
    return total


def _drain_iostream_prefetch(
    stream: Any, file_fd: int, file_offset: int
) -> tuple[int, int]:
    """Write bytes Tornado already buffered from the socket before raw recv."""
    size = int(getattr(stream, "_read_buffer_size", 0) or 0)
    if size <= 0:
        return 0, file_offset
    buf = bytes(stream._read_buffer[:size])
    del stream._read_buffer[:size]
    stream._read_buffer_size = 0
    if file_offset:
        pwrite_fd(file_fd, buf, file_offset)
    else:
        write_fd(file_fd, buf)
    n = len(buf)
    return n, file_offset + n


def _dup_blocking_socket_fd(sock: socket.socket) -> int:
    dup_fd = os.dup(sock.fileno())
    try:
        dup_sock = socket.socket(sock.family, sock.type, sock.proto, fileno=dup_fd)
        dup_sock.setblocking(True)
        dup_sock.detach()
    except OSError:
        flags = fcntl.fcntl(dup_fd, fcntl.F_GETFL)
        fcntl.fcntl(dup_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
    return dup_fd


def _ssl_blocking_recv_to_fd(
    ssl_sock: ssl.SSLSocket,
    file_fd: int,
    max_bytes: int,
    file_offset: int,
    cancel: Any | None,
    *,
    chunk_size: int = 8 * 1024 * 1024,
) -> int:
    """TLS cannot splice; blocking recv → Rust pwrite (one decrypt copy)."""
    ssl_sock.setblocking(True)
    remaining = int(max_bytes)
    total = 0
    pos = int(file_offset)
    while remaining > 0:
        if cancel is not None and getattr(cancel, "is_cancelled", lambda: False)():
            break
        chunk = ssl_sock.recv(min(remaining, chunk_size))
        if not chunk:
            break
        if pos:
            pwrite_fd(file_fd, chunk, pos)
        else:
            write_fd(file_fd, chunk)
        n = len(chunk)
        total += n
        remaining -= n
        pos += n
    return total


async def zcopy_pump_detached(
    stream: Any,
    file_fd: int,
    max_bytes: int,
    cancel: Any | None = None,
    *,
    file_offset: int = 0,
) -> int:
    """Socket→disk without Python per-chunk copies (Linux splice or recv→pwrite)."""
    tune_transfer_stream(stream)
    written, pos = await asyncio.to_thread(
        _drain_iostream_prefetch, stream, file_fd, file_offset
    )
    remaining = int(max_bytes) - written
    if remaining <= 0:
        return written

    sock = getattr(stream, "socket", None)
    if sock is None:
        extra = await pump_detached_stream_to_fd(
            stream, file_fd, remaining, cancel, file_offset=pos
        )
        return written + extra

    if isinstance(sock, ssl.SSLSocket):
        extra = await asyncio.to_thread(
            _ssl_blocking_recv_to_fd, sock, file_fd, remaining, pos, cancel
        )
        return written + extra

    dup_fd = await asyncio.to_thread(_dup_blocking_socket_fd, sock)
    try:
        pump_cancel = cancel if cancel is not None else new_cancel_flag()
        extra = await asyncio.to_thread(
            recv_to_fd, dup_fd, file_fd, remaining, pump_cancel, pos
        )
    finally:
        try:
            os.close(dup_fd)
        except OSError:
            pass
    return written + extra


def recv_to_fd(
    sock_fd: int,
    file_fd: int,
    max_bytes: int,
    cancel: Any | None = None,
    file_offset: int = 0,
) -> int:
    mod = _load_extension()
    if mod is None:
        raise RuntimeError("aird_transfer extension is not installed")
    return int(mod.recv_to_fd(sock_fd, file_fd, max_bytes, cancel, file_offset))


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
        *,
        file_offset: int = 0,
        close_file_fd: bool = True,
    ) -> None:
        self._sock_fd = sock_fd
        self._file_fd = file_fd
        self._nbytes = nbytes
        self._file_offset = file_offset
        self._cancel = cancel
        self._close_file_fd = close_file_fd
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
                self._file_offset,
            )
        except Exception as exc:
            self.error = exc
            logger.warning("Native upload pump failed: %s", exc)
        finally:
            if self._close_file_fd and self._file_fd >= 0:
                try:
                    os.close(self._file_fd)
                except OSError:
                    pass
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
    *,
    file_offset: int = 0,
    close_file_fd: bool = False,
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
    pump = NativeSocketUploadPump(
        sock_fd,
        file_fd,
        nbytes,
        cancel,
        file_offset=file_offset,
        close_file_fd=close_file_fd,
    )
    pump.start()
    handler._native_pump = pump
    handler._native_cancel = cancel
    logger.debug(
        "Native upload pump started (%d bytes at offset %d, sock=%d file=%d)",
        nbytes,
        file_offset,
        sock_fd,
        file_fd,
    )
    return True
