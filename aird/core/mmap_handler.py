"""Memory-mapped file handler for efficient large file operations."""

import asyncio
import os
import mmap

import aiofiles
from aird.constants import MMAP_MIN_SIZE, CHUNK_SIZE


def _read_chunks_sync(
    file_path: str, start: int, end: int | None, file_size: int, chunk_size: int
) -> list[bytes]:
    """Read file chunks synchronously (for mmap path or fallback)."""
    remaining = (end - start + 1) if end is not None else file_size - start
    chunks = []
    with open(file_path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    return chunks


def _read_chunks_mmap(
    file_path: str, start: int, end: int | None, file_size: int, chunk_size: int
) -> list[bytes]:
    """Read file chunks using mmap."""
    chunks = []
    with open(file_path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            actual_end = min(end or file_size - 1, file_size - 1)
            current = start
            while current <= actual_end:
                chunk_end = min(current + chunk_size, actual_end + 1)
                chunks.append(mm[current:chunk_end])
                current = chunk_end
    return chunks


class MMapFileHandler:
    """Efficient file handling using memory mapping for large files"""

    @staticmethod
    def should_use_mmap(file_size: int) -> bool:
        """Determine if mmap should be used based on file size"""
        return file_size >= MMAP_MIN_SIZE

    @staticmethod
    async def serve_file_chunk(
        file_path: str, start: int = 0, end: int = None, chunk_size: int = CHUNK_SIZE
    ):
        """Serve file chunks using mmap for efficient memory usage"""
        try:
            file_size = await asyncio.to_thread(os.path.getsize, file_path)

            if not MMapFileHandler.should_use_mmap(file_size):
                # Use async file API for small files
                remaining = (end - start + 1) if end is not None else file_size - start
                async with aiofiles.open(file_path, "rb") as f:
                    await f.seek(start)
                    while remaining > 0:
                        chunk = await f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        yield chunk
                        remaining -= len(chunk)
                return

            # Use mmap for large files (run in thread pool - mmap requires sync fd)
            chunks = await asyncio.to_thread(
                _read_chunks_mmap, file_path, start, end, file_size, chunk_size
            )
            for chunk in chunks:
                yield chunk

        except (OSError, ValueError):
            # Fallback to traditional method on mmap errors (run in thread pool)
            file_size = await asyncio.to_thread(os.path.getsize, file_path)
            chunks = await asyncio.to_thread(
                _read_chunks_sync, file_path, start, end, file_size, chunk_size
            )
            for chunk in chunks:
                yield chunk

    @staticmethod
    def find_line_offsets(file_path: str, max_lines: int = None) -> list[int]:
        """Efficiently find line start offsets using mmap"""
        try:
            file_size = os.path.getsize(file_path)
            if not MMapFileHandler.should_use_mmap(file_size):
                return _find_offsets_small(file_path, max_lines)
            return _find_offsets_mmap(file_path, max_lines)
        except (OSError, ValueError):
            return _find_offsets_small(file_path, max_lines)

    @staticmethod
    def search_in_file(
        file_path: str, search_term: str, max_results: int = 100
    ) -> list[dict]:
        """Efficiently search for text in file using mmap"""
        try:
            file_size = os.path.getsize(file_path)
            search_bytes = search_term.encode("utf-8")
            if not MMapFileHandler.should_use_mmap(file_size):
                return _search_small_file(file_path, search_term, max_results)
            return _search_mmap_file(file_path, search_term, search_bytes, max_results)
        except (OSError, UnicodeDecodeError):
            return _search_small_file(file_path, search_term, max_results)


def _find_offsets_small(file_path: str, max_lines: int | None) -> list[int]:
    """Find line offsets using traditional file iteration."""
    offsets = [0]
    with open(file_path, "rb") as f:
        pos = 0
        for line in f:
            pos += len(line)
            offsets.append(pos)
            if max_lines and len(offsets) > max_lines:
                break
    return offsets[:-1]


def _find_offsets_mmap(file_path: str, max_lines: int | None) -> list[int]:
    """Find line offsets using mmap."""
    offsets = [0]
    with open(file_path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            pos = 0
            while pos < len(mm):
                newline_pos = mm.find(b"\n", pos)
                if newline_pos == -1:
                    break
                pos = newline_pos + 1
                offsets.append(pos)
                if max_lines and len(offsets) > max_lines:
                    break
    return offsets[:-1]


def _match_positions(line_content: str, search_term: str) -> list[int]:
    """Find all match positions of search_term in line_content."""
    positions = []
    start_pos = 0
    while True:
        pos = line_content.find(search_term, start_pos)
        if pos == -1:
            break
        positions.append(pos)
        start_pos = pos + 1
    return positions


def _search_small_file(
    file_path: str, search_term: str, max_results: int
) -> list[dict]:
    """Search in small file using traditional file reading."""
    results = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            if search_term in line:
                results.append(
                    {
                        "line_number": line_num,
                        "line_content": line.rstrip("\n"),
                        "match_positions": [
                            i
                            for i in range(len(line))
                            if line[i:].startswith(search_term)
                        ],
                    }
                )
                if len(results) >= max_results:
                    break
    return results


def _search_mmap_file(
    file_path: str, search_term: str, search_bytes: bytes, max_results: int
) -> list[dict]:
    """Search in large file using mmap."""
    results = []
    with open(file_path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            current_pos = 0
            line_number = 1
            while current_pos < len(mm) and len(results) < max_results:
                newline_pos = mm.find(b"\n", current_pos)
                if newline_pos == -1:
                    line_bytes = mm[current_pos:]
                    if search_bytes in line_bytes:
                        line_content = line_bytes.decode("utf-8", errors="replace")
                        results.append(
                            {
                                "line_number": line_number,
                                "line_content": line_content,
                                "match_positions": _match_positions(
                                    line_content, search_term
                                ),
                            }
                        )
                    break
                line_bytes = mm[current_pos:newline_pos]
                if search_bytes in line_bytes:
                    line_content = line_bytes.decode("utf-8", errors="replace")
                    results.append(
                        {
                            "line_number": line_number,
                            "line_content": line_content,
                            "match_positions": _match_positions(
                                line_content, search_term
                            ),
                        }
                    )
                current_pos = newline_pos + 1
                line_number += 1
    return results
