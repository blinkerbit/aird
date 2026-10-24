"""Spool a chat upload to disk and split multipart parts without holding file bytes in RAM."""

from __future__ import annotations

import os
import re
import tempfile
from urllib.parse import parse_qs

_CHUNK = 64 * 1024
_FIELD_MAX = 2 * 1024 * 1024
_DISPOSITION = re.compile(
    r"""filename\*=UTF-8''([^;]+)|filename="([^"]*)"|filename=([^;\s]+)|name="([^"]*)"|name=([^;\s]+)""",
    re.IGNORECASE,
)


def parse_spooled_form(path: str, content_type: str) -> tuple[dict[str, list[str]], list[dict]]:
    """Return form fields and file dicts. File dicts use tmp_path instead of body."""
    ctype = content_type or ""
    lowered = ctype.lower()
    if "multipart/form-data" not in lowered:
        if "application/x-www-form-urlencoded" in lowered:
            return _urlencoded(path), []
        return {}, []
    boundary = _boundary(ctype)
    if not boundary:
        raise ValueError("Missing upload boundary")
    marker = b"\r\n--" + boundary.encode("utf-8", "replace")
    first = b"--" + boundary.encode("utf-8", "replace")
    fields: dict[str, list[str]] = {}
    files: list[dict] = []
    created: list[str] = []
    try:
        return _read_multipart(path, first, marker, fields, files, created)
    except Exception:
        for tmp in created:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def _read_multipart(path, first, marker, fields, files, created) -> tuple[dict[str, list[str]], list[dict]]:
    with open(path, "rb") as src:
        if not _drain_until(src, first, None):
            return fields, files
        while True:
            nxt = src.read(2)
            if nxt == b"--" or nxt == b"":
                break
            if nxt != b"\r\n":
                src.seek(-len(nxt), os.SEEK_CUR)
            headers = _read_headers(src)
            name, filename, mime = _part_meta(headers)
            if filename is not None:
                fd, tmp = tempfile.mkstemp(prefix="aird_chat_")
                os.close(fd)
                created.append(tmp)
                with open(tmp, "wb") as dest:
                    found = _drain_until(src, marker, dest)
                if not found:
                    os.unlink(tmp)
                    break
                files.append(
                    {
                        "filename": filename or "file",
                        "body": b"",
                        "content_type": mime or "application/octet-stream",
                        "tmp_path": tmp,
                    }
                )
            else:
                found, raw = _drain_field(src, marker)
                if name:
                    fields.setdefault(name, []).append(raw.decode("utf-8", "replace"))
                if not found:
                    break
    return fields, files


def _urlencoded(path: str) -> dict[str, list[str]]:
    with open(path, "rb") as src:
        raw = src.read(_FIELD_MAX + 1)
    if len(raw) > _FIELD_MAX:
        raise ValueError("Form is too large")
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {key: list(values) for key, values in parsed.items()}


def _boundary(content_type: str) -> str:
    match = re.search(r"boundary=([^;]+)", content_type, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip().strip('"')


def _read_headers(src) -> bytes:
    raw = b""
    while b"\r\n\r\n" not in raw and b"\n\n" not in raw:
        line = src.readline()
        if not line:
            break
        raw += line
        if len(raw) > 65536:
            raise ValueError("Upload headers too large")
    return raw


def _part_meta(headers: bytes) -> tuple[str, str | None, str]:
    text = headers.decode("utf-8", "replace")
    name = ""
    filename = None
    mime = ""
    for line in text.splitlines():
        if line.lower().startswith("content-disposition:"):
            name, filename = _disposition(line)
        elif line.lower().startswith("content-type:"):
            mime = line.split(":", 1)[1].strip()
    return name, filename, mime


def _disposition(line: str) -> tuple[str, str | None]:
    name = ""
    filename = None
    for match in _DISPOSITION.finditer(line):
        star, quoted, bare, named, name_bare = match.groups()
        if star or quoted is not None or bare is not None:
            filename = star or quoted or bare or ""
        elif named or name_bare:
            name = named or name_bare or ""
    return name, filename


def _drain_until(src, marker: bytes, dest) -> bool:
    overlap = b""
    keep = max(len(marker) - 1, 0)
    while True:
        start = src.tell()
        block = src.read(_CHUNK)
        data = overlap + block
        base = start - len(overlap)
        idx = data.find(marker)
        if idx >= 0:
            if dest is not None and idx:
                dest.write(data[:idx])
            src.seek(base + idx + len(marker))
            return True
        if not block:
            return False
        if len(data) > keep:
            emit_end = len(data) - keep
            if dest is not None:
                dest.write(data[:emit_end])
            overlap = data[emit_end:]
        else:
            overlap = data


def _drain_field(src, marker: bytes) -> tuple[bool, bytes]:
    fd, tmp = tempfile.mkstemp(prefix="aird_chat_field_")
    os.close(fd)
    try:
        with open(tmp, "wb") as dest:
            found = _drain_until(src, marker, dest)
        size = os.path.getsize(tmp)
        if size > _FIELD_MAX:
            raise ValueError("Form field is too large")
        with open(tmp, "rb") as src_field:
            return found, src_field.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
