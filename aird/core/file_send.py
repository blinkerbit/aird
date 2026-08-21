"""Zero-copy file send: Linux os.sendfile / Windows TransmitFile."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import sys

from aird.core.transfer_native import (
    _blocking_socket_fd_for_pump,
    _dup_blocking_socket_fd,
    _load_extension,
)

logger = logging.getLogger(__name__)

_SENDFILE_CHUNK = 8 * 1024 * 1024


def sendfile_available() -> bool:
    if sys.platform.startswith("linux") and hasattr(os, "sendfile"):
        return True
    if sys.platform == "win32":
        mod = _load_extension()
        return mod is not None and getattr(mod, "transmit_file_available", lambda: False)()
    return False


def _sendfile_sync(out_fd: int, in_fd: int, offset: int, count: int) -> int:
    sent = 0
    while sent < count:
        n = os.sendfile(out_fd, in_fd, offset + sent, min(_SENDFILE_CHUNK, count - sent))
        if n <= 0:
            break
        sent += n
    return sent


def _transmit_file_sync(sock_fd: int, file_path: str, start: int, count: int) -> int:
    mod = _load_extension()
    if mod is None:
        return 0
    return int(mod.transmit_file(sock_fd, file_path, start, count))


async def sendfile_to_socket(
    sock,
    file_path: str,
    start: int = 0,
    length: int | None = None,
) -> bool:
    """Send file bytes to socket via sendfile/TransmitFile. Returns False on failure."""
    if not sendfile_available():
        return False
    if isinstance(sock, ssl.SSLSocket):
        return False
    try:
        sock.fileno()
    except (AttributeError, OSError):
        return False
    try:
        file_size = os.path.getsize(file_path)
        end = file_size if length is None else start + length
        count = min(end, file_size) - start
        if count <= 0:
            return True

        def _run() -> int:
            # Tornado sockets are non-blocking; kernel send APIs need blocking.
            if sys.platform == "win32":
                out_fd, _should_close = _blocking_socket_fd_for_pump(sock)
                # SOCKET is owned by the stream — do not closesocket() it here.
                return _transmit_file_sync(out_fd, file_path, start, count)

            out_fd = _dup_blocking_socket_fd(sock)
            try:
                with open(file_path, "rb") as f:
                    in_fd = f.fileno()
                    return _sendfile_sync(out_fd, in_fd, start, count)
            finally:
                try:
                    os.close(out_fd)
                except OSError:
                    pass

        sent = await asyncio.to_thread(_run)
        return sent >= count
    except OSError:
        logger.debug("sendfile failed for %s", file_path, exc_info=True)
        return False
