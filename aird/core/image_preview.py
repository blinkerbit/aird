"""Embedded JPEG previews inside camera RAW files (DNG, CR2, …)."""

from __future__ import annotations

import mmap
import os

from aird.constants.media import IMAGE_RAW_EXTENSIONS

_JPEG_SOI = b"\xff\xd8\xff"
_JPEG_EOI = b"\xff\xd9"
_MIN_PREVIEW_BYTES = 4096


def _largest_jpeg(data) -> bytes | None:
    best = b""
    start = 0
    find = data.find
    while True:
        i = find(_JPEG_SOI, start)
        if i < 0:
            break
        j = find(_JPEG_EOI, i + 3)
        if j > i:
            blob = bytes(data[i : j + 2])
            if len(blob) > len(best):
                best = blob
        start = i + 3
    if len(best) < _MIN_PREVIEW_BYTES:
        return None
    return best


def embedded_jpeg_preview(abspath: str) -> bytes | None:
    """Return the largest JPEG stream embedded in ``abspath``, if any."""
    try:
        size = os.path.getsize(abspath)
        if size < _MIN_PREVIEW_BYTES:
            return None
        with open(abspath, "rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return _largest_jpeg(mm)
    except (OSError, ValueError):
        try:
            with open(abspath, "rb") as fh:
                return _largest_jpeg(fh.read())
        except OSError:
            return None


def inline_preview_payload(abspath: str) -> tuple[bytes, str] | None:
    """JPEG preview + mime for camera RAW; otherwise ``None`` (serve the file)."""
    ext = os.path.splitext(abspath)[1].lower()
    if ext not in IMAGE_RAW_EXTENSIONS:
        return None
    jpeg = embedded_jpeg_preview(abspath)
    if not jpeg:
        return None
    return jpeg, "image/jpeg"
