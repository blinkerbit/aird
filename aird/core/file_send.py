"""Zero-copy file send via os.sendfile (Linux)."""

from __future__ import annotations

import asyncio
import logging
import os
import select
import sys

logger = logging.getLogger(__name__)

_SENDFILE_CHUNK = 8 * 1024 * 1024


def sendfile_available() -> bool:
    return sys.platform.startswith("linux") and hasattr(os, "sendfile")


def _wait_socket_writable(out_fd: int) -> None:
    """Tornado sockets are non-blocking; sendfile stops with EAGAIN when the buffer fills."""
    while True:
        try:
            _, ready, _ = select.select([], [out_fd], [], 30)
        except InterruptedError:
            continue
        if ready:
            return
        raise TimeoutError("timed out waiting to send file")


def _sendfile_sync(out_fd: int, in_fd: int, offset: int, count: int) -> int:
    sent = 0
    while sent < count:
        try:
            n = os.sendfile(
                out_fd, in_fd, offset + sent, min(_SENDFILE_CHUNK, count - sent)
            )
        except BlockingIOError:
            _wait_socket_writable(out_fd)
            continue
        except InterruptedError:
            continue
        if n <= 0:
            break
        sent += n
    return sent


async def sendfile_to_socket(
    sock,
    file_path: str,
    start: int = 0,
    length: int | None = None,
) -> int:
    """Send file bytes to socket via sendfile.

    Returns the number of bytes written. ``0`` means nothing was sent and the
    caller may still write the body. ``-1`` means some or all bytes may already
    be on the socket and the caller must not write the body again.
    """
    if not sendfile_available():
        return 0
    try:
        out_fd = sock.fileno()
    except (AttributeError, OSError):
        return 0
    try:
        if ".." in file_path:
            raise Exception("Invalid file path")
        file_size = os.path.getsize(file_path)
        end = file_size if length is None else start + length
        count = min(end, file_size) - start
        if count <= 0:
            return 0

        def _run() -> int:
            with open(file_path, "rb") as f:
                try:
                    return _sendfile_sync(out_fd, f.fileno(), start, count)
                except OSError:
                    logger.debug("sendfile failed for %s", file_path, exc_info=True)
                    return -1

        return await asyncio.to_thread(_run)
    except OSError:
        logger.debug("sendfile failed for %s", file_path, exc_info=True)
        return 0
