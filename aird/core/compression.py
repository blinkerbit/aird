"""HTTP response compression: zstd, brotli, gzip with streaming."""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_COMPRESSIBLE_MIME_PREFIXES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
)

_INCOMPRESSIBLE_EXTENSIONS = frozenset(
    {
        ".gz",
        ".zip",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".mp4",
        ".webm",
        ".mp3",
        ".wav",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".ico",
        ".pdf",
        ".br",
        ".zst",
        ".zstd",
    }
)

_zstd_available = False
_brotli_available = False

try:
    import zstandard as _zstd  # noqa: F401

    _zstd_available = True
except ImportError:
    _zstd = None

try:
    import brotli as _brotli  # noqa: F401

    _brotli_available = True
except ImportError:
    _brotli = None


def codecs_available() -> dict[str, bool]:
    return {"zstd": _zstd_available, "br": _brotli_available, "gzip": True}


def negotiate_encoding(accept_header: str | None, enabled: list[str] | None = None) -> str | None:
    """Pick best Content-Encoding from Accept-Encoding (zstd > br > gzip)."""
    if not accept_header:
        return None
    allowed = enabled or ["zstd", "br", "gzip"]
    tokens: dict[str, float] = {}
    for part in accept_header.split(","):
        piece = part.strip()
        if not piece:
            continue
        if ";" in piece:
            name, _, qpart = piece.partition(";")
            qval = 1.0
            if "q=" in qpart:
                try:
                    qval = float(qpart.split("q=", 1)[1].strip())
                except ValueError:
                    qval = 0.0
        else:
            name, qval = piece, 1.0
        name = name.strip().lower()
        if qval > 0:
            tokens[name] = max(tokens.get(name, 0.0), qval)
    for codec in ("zstd", "br", "gzip"):
        if codec in allowed and codec in tokens:
            if codec == "zstd" and not _zstd_available:
                continue
            if codec == "br" and not _brotli_available:
                continue
            return codec
    return None


def _ip_in_corporate(remote_ip: str, cidrs: list[str]) -> bool:
    if not remote_ip or not cidrs:
        return False
    try:
        import ipaddress

        addr = ipaddress.ip_address(remote_ip)
        for cidr in cidrs:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
    except ValueError:
        pass
    return False


def should_compress(
    *,
    path: str,
    mime_type: str,
    file_size: int,
    has_range: bool,
    remote_ip: str,
    compression_enabled: bool,
    mode: str = "wan_only",
    min_bytes: int = 1024,
    max_bytes: int = 50 * 1024 * 1024,
    corporate_cidrs: list[str] | None = None,
) -> bool:
    if not compression_enabled or has_range:
        return False
    if file_size < min_bytes or file_size > max_bytes:
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext in _INCOMPRESSIBLE_EXTENSIONS:
        return False
    if not any(mime_type.startswith(p) for p in _COMPRESSIBLE_MIME_PREFIXES):
        return False
    if mode == "never":
        return False
    if mode == "wan_only" and _ip_in_corporate(remote_ip, corporate_cidrs or []):
        return False
    return True


def _compress_file_sync(path: str, encoding: str, level: int) -> bytes:
    with open(path, "rb") as f_in:
        raw = f_in.read()
    if encoding == "zstd":
        cctx = _zstd.ZstdCompressor(level=level)
        return cctx.compress(raw)
    if encoding == "br":
        return _brotli.compress(raw, quality=min(level, 11))
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=min(level, 9)) as gz:
        gz.write(raw)
    return buf.getvalue()


async def compress_file(path: str, encoding: str, level: int = 6) -> bytes:
    return await asyncio.to_thread(_compress_file_sync, path, encoding, level)


async def stream_uncompressed(path: str, chunk_size: int = 65536) -> AsyncIterator[bytes]:
    with open(path, "rb") as f:
        while True:
            chunk = await asyncio.to_thread(f.read, chunk_size)
            if not chunk:
                break
            yield chunk
