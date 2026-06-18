"""HTTP Range (RFC 7233) parsing for downloads and Content-Range uploads."""

from __future__ import annotations

import re
from dataclasses import dataclass

_RANGE_RE = re.compile(r"^(\d*)-(\d*)$")
_CONTENT_RANGE_RE = re.compile(
    r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range_header(header: str | None, file_size: int) -> ByteRange | None:
    """Parse a single ``Range: bytes=`` value. Returns None if unsatisfied or invalid."""
    if not header or file_size <= 0:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None
    spec = header.split("=", 1)[1].strip()
    if "," in spec:
        spec = spec.split(",", 1)[0].strip()
    m = _RANGE_RE.match(spec)
    if not m:
        return None
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        suffix = int(end_s)
        if suffix <= 0:
            return None
        start = max(0, file_size - suffix)
        end = file_size - 1
    elif end_s == "":
        start = int(start_s)
        end = file_size - 1
    else:
        start = int(start_s)
        end = int(end_s)
    if start < 0 or end < start or start >= file_size:
        return None
    end = min(end, file_size - 1)
    return ByteRange(start, end)


def parse_content_range(header: str | None) -> tuple[int, int, int | None] | None:
    """Return (start, end, total) from Content-Range or None."""
    if not header:
        return None
    m = _CONTENT_RANGE_RE.match(header.strip())
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    total_s = m.group(3)
    total = None if total_s == "*" else int(total_s)
    if end < start:
        return None
    return start, end, total


def merge_ranges(ranges: list[ByteRange]) -> list[ByteRange]:
    """Merge overlapping/adjacent ranges sorted by start."""
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda r: r.start)
    merged: list[ByteRange] = [sorted_ranges[0]]
    for current in sorted_ranges[1:]:
        last = merged[-1]
        if current.start <= last.end + 1:
            merged[-1] = ByteRange(last.start, max(last.end, current.end))
        else:
            merged.append(current)
    return merged


def ranges_cover_file(ranges: list[ByteRange], total_size: int) -> bool:
    """True if merged ranges cover [0, total_size)."""
    if total_size <= 0:
        return False
    merged = merge_ranges(ranges)
    if not merged or merged[0].start != 0:
        return False
    covered = 0
    for r in merged:
        if r.start != covered:
            return False
        covered = r.end + 1
    return covered >= total_size


def ranges_to_json(ranges: list[ByteRange]) -> list[list[int]]:
    return [[r.start, r.end] for r in merge_ranges(ranges)]


def ranges_from_json(data: list) -> list[ByteRange]:
    out: list[ByteRange] = []
    for item in data or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        out.append(ByteRange(int(item[0]), int(item[1])))
    return merge_ranges(out)
