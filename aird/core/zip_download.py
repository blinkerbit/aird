"""Build ZIP archives from user file paths."""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from typing import Iterable

from aird.core.security import is_within_root

logger = logging.getLogger(__name__)

MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


class ZipDownloadError(Exception):
    """Raised when a zip cannot be built."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _normalise_rel_path(path: str) -> str:
    return path.replace("\\", "/").strip().strip("/")


def _safe_arcname(rel_path: str) -> str | None:
    parts = [p for p in _normalise_rel_path(rel_path).split("/") if p and p not in (".", "..")]
    if not parts:
        return None
    return "/".join(parts)


def collect_zip_entries(root_dir: str, paths: Iterable[str]) -> list[tuple[str, str]]:
    """Return (absolute_path, archive_name) pairs for all files under *paths*."""
    root_dir = os.path.realpath(root_dir)
    entries: list[tuple[str, str]] = []
    seen_arc: set[str] = set()
    total_bytes = 0

    for raw in paths:
        if not isinstance(raw, str):
            raise ZipDownloadError("Invalid path", 400)
        rel = _normalise_rel_path(raw)
        if not rel:
            continue
        abspath = os.path.realpath(os.path.join(root_dir, rel))
        if not is_within_root(abspath, root_dir):
            raise ZipDownloadError("Access denied", 403)
        if not os.path.exists(abspath):
            raise ZipDownloadError(f"Not found: {rel}", 404)

        def add_file(file_abs: str, arc: str | None) -> None:
            nonlocal total_bytes
            if arc is None:
                return
            if arc in seen_arc:
                return
            try:
                size = os.path.getsize(file_abs)
            except OSError:
                return
            total_bytes += size
            if total_bytes > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ZipDownloadError("Selection is too large to zip", 413)
            seen_arc.add(arc)
            entries.append((file_abs, arc))
            if len(entries) > MAX_ZIP_ENTRIES:
                raise ZipDownloadError("Too many files in selection", 400)

        if os.path.isfile(abspath):
            add_file(abspath, _safe_arcname(rel))
            continue

        if os.path.isdir(abspath):
            prefix = _safe_arcname(rel)
            for dirpath, _dirnames, filenames in os.walk(abspath):
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    if not os.path.isfile(full):
                        continue
                    file_rel = os.path.relpath(full, root_dir).replace("\\", "/")
                    add_file(full, _safe_arcname(file_rel))
            continue

        raise ZipDownloadError(f"Not a file or folder: {rel}", 400)

    return entries


def build_zip_file(entries: list[tuple[str, str]]) -> str:
    """Write entries to a temporary store-only zip (no compression, low CPU)."""
    if not entries:
        raise ZipDownloadError("No files to download", 400)

    fd, zip_path = tempfile.mkstemp(prefix="aird_zip_", suffix=".zip")
    os.close(fd)
    try:
        # ZIP_STORED: pack files without deflate — minimal CPU; larger wire size.
        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as zf:
            for file_abs, arcname in entries:
                zf.write(file_abs, arcname, compress_type=zipfile.ZIP_STORED)
    except Exception:
        logger.exception("ZIP build failed")
        try:
            os.remove(zip_path)
        except OSError:
            pass
        raise ZipDownloadError("Failed to create zip archive", 500) from None
    return zip_path
