"""Socketify-native file transfer (libuv-owned sockets, no Tornado IOCP).

Upload/download bodies use res.on_data / chunked res.write; aird_transfer
accelerates write_fd when the extension is installed.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import tempfile
from typing import Any, Callable

from aird.constants import CHUNK_SIZE
from aird.core.transfer_native import (
    native_available,
    new_cancel_flag,
    write_fd,
)

logger = logging.getLogger(__name__)

_WRITE_CHUNK = max(CHUNK_SIZE, 4 * 1024 * 1024)


def _plain_end(res: Any, status: int, message: str) -> None:
    body = message.encode("utf-8") if isinstance(message, str) else message

    def _send(r: Any) -> None:
        r.write_status(status)
        r.write_header("Content-Type", "text/plain; charset=UTF-8")
        r.write_header("Content-Length", str(len(body)))
        r.end(body)

    try:
        res.cork(_send)
    except Exception:
        _send(res)


async def stream_upload_on_data(
    res: Any,
    *,
    dest_dir: str,
    expected_bytes: int,
    on_complete: Callable[[str, int], Any],
    on_error: Callable[[int, str], Any] | None = None,
) -> None:
    """L1 upload: stream request body into a staging file via on_data + write_fd."""
    os.makedirs(dest_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".aird_up_", dir=dest_dir)
    received = 0
    failed = False
    cancel = new_cancel_flag() if native_available() else None

    def _fail(status: int, msg: str) -> None:
        nonlocal failed
        failed = True
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        if on_error:
            on_error(status, msg)
        else:
            _plain_end(res, status, msg)

    def on_data(r: Any, chunk: bytes, is_end: bool) -> None:
        nonlocal received, failed
        if failed or getattr(r, "aborted", False):
            return
        if chunk:
            try:
                write_fd(fd, chunk)
            except OSError:
                logger.exception("upload write failed")
                _fail(500, "Upload save failed")
                return
            received += len(chunk)
            if expected_bytes > 0 and received > expected_bytes:
                _fail(413, "File too large")
                return
        if not is_end:
            return
        try:
            os.close(fd)
        except OSError:
            pass
        if expected_bytes > 0 and received != expected_bytes:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            _fail(499, "Upload incomplete")
            return
        try:
            result = on_complete(temp_path, received)
            if asyncio.iscoroutine(result):
                r.run_async(result)
        except Exception:
            logger.exception("upload finalize failed")
            _fail(500, "Upload save failed")

    def on_abort(r: Any) -> None:
        nonlocal failed
        r.aborted = True
        failed = True
        if cancel is not None:
            try:
                cancel.cancel()
            except Exception:
                pass
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass

    res.on_aborted(on_abort)
    res.on_data(on_data)


async def stream_upload_range_on_data(
    res: Any,
    *,
    write_chunk: Callable[[int, bytes], int],
    file_offset: int,
    expected_bytes: int,
    on_complete: Callable[[int], Any],
    on_error: Callable[[int, str], Any] | None = None,
) -> None:
    """Write one ranged chunk via on_data; *write_chunk(abs_offset, data)* must be thread-safe."""
    received = 0
    failed = False

    def _fail(status: int, msg: str) -> None:
        nonlocal failed
        failed = True
        if on_error:
            on_error(status, msg)
        else:
            _plain_end(res, status, msg)

    def on_data(r: Any, chunk: bytes, is_end: bool) -> None:
        nonlocal received, failed
        if failed or getattr(r, "aborted", False):
            return
        if chunk:
            try:
                write_chunk(file_offset + received, chunk)
            except OSError:
                logger.exception("ranged upload write failed")
                _fail(500, "Upload save failed")
                return
            except ValueError as exc:
                _fail(400, str(exc) or "Bad chunk")
                return
            received += len(chunk)
            if expected_bytes > 0 and received > expected_bytes:
                _fail(413, "Chunk too large")
                return
        if not is_end:
            return
        if expected_bytes > 0 and received != expected_bytes:
            _fail(499, "Upload incomplete")
            return
        try:
            result = on_complete(received)
            if asyncio.iscoroutine(result):
                r.run_async(result)
        except Exception:
            logger.exception("ranged upload complete failed")
            _fail(500, "Upload save failed")

    def on_abort(r: Any) -> None:
        nonlocal failed
        r.aborted = True
        failed = True

    res.on_aborted(on_abort)
    res.on_data(on_data)


async def stream_download_chunks(
    res: Any,
    req: Any,
    file_path: str,
    *,
    filename: str | None = None,
    content_type: str | None = None,
    as_attachment: bool = True,
) -> None:
    """L1 download: corked chunked reads into res.write / end."""
    if not os.path.isfile(file_path):
        _plain_end(res, 404, "Not Found")
        return

    try:
        from socketify import sendfile as socketify_sendfile

        # Prefer socketify helper (Range / 304) when not forcing attachment name.
        if not as_attachment and filename is None:
            await socketify_sendfile(res, req, file_path)
            return
    except Exception:
        logger.debug("socketify sendfile unavailable", exc_info=True)

    size = os.path.getsize(file_path)
    ctype = content_type or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    disp_name = filename or os.path.basename(file_path)

    def _headers(r: Any) -> None:
        r.write_status(200)
        r.write_header("Content-Type", ctype)
        r.write_header("Content-Length", str(size))
        if as_attachment:
            r.write_header(
                "Content-Disposition",
                f'attachment; filename="{disp_name}"',
            )
        r.write_header("Cache-Control", "no-store")

    try:
        res.cork(_headers)
    except Exception:
        _headers(res)

    remaining = size
    try:
        with open(file_path, "rb") as fh:
            while remaining > 0:
                if getattr(res, "aborted", False):
                    return
                chunk = fh.read(min(_WRITE_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                if remaining <= 0:
                    res.end(chunk)
                    return
                ok = res.write(chunk)
                if ok is False:
                    # backpressure — small yield
                    await asyncio.sleep(0)
        if not res.has_responded():
            res.end(b"")
    except Exception:
        logger.exception("chunked download failed for %s", file_path)
        if not res.has_responded():
            _plain_end(res, 500, "Download failed")
