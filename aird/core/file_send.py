"""Zero-copy file send via os.sendfile (Linux)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

_SENDFILE_CHUNK = 8 * 1024 * 1024


def sendfile_available() -> bool:
    return sys.platform.startswith("linux") and hasattr(os, "sendfile")


def _sendfile_sync(out_fd: int, in_fd: int, offset: int, count: int) -> int:
    sent = 0
    while sent < count:
        n = os.sendfile(out_fd, in_fd, offset + sent, min(_SENDFILE_CHUNK, count - sent))
        if n <= 0:
            break
        sent += n
    return sent


async def sendfile_to_socket(
    sock,
    file_path: str,
    start: int = 0,
    length: int | None = None,
) -> bool:
    """Send file bytes to socket via sendfile. Returns False if unsupported or failed."""
    if not sendfile_available():
        return False
    try:
        out_fd = sock.fileno()
    except (AttributeError, OSError):
        return False
    try:
        file_size = os.path.getsize(file_path)
        end = file_size if length is None else start + length
        count = min(end, file_size) - start
        if count <= 0:
            return True

        def _run() -> int:
            with open(file_path, "rb") as f:
                in_fd = f.fileno()
                return _sendfile_sync(out_fd, in_fd, start, count)

        sent = await asyncio.to_thread(_run)
        return sent >= count
    except OSError:
        logger.debug("sendfile failed for %s", file_path, exc_info=True)
        return False
