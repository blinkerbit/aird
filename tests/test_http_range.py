"""Tests for HTTP Range parsing and ranged upload coverage."""

import pytest

from aird.core.http_range import (
    ByteRange,
    merge_ranges,
    parse_content_range,
    parse_range_header,
    ranges_cover_file,
    ranges_from_json,
    ranges_to_json,
)


def test_parse_range_header_full_file():
    assert parse_range_header("bytes=0-", 1000) == ByteRange(0, 999)


def test_parse_range_header_suffix():
    assert parse_range_header("bytes=-100", 1000) == ByteRange(900, 999)


def test_parse_range_header_middle():
    assert parse_range_header("bytes=100-199", 1000) == ByteRange(100, 199)


def test_parse_content_range():
    assert parse_content_range("bytes 0-1023/5000") == (0, 1023, 5000)


def test_merge_and_cover():
    ranges = merge_ranges([ByteRange(0, 10), ByteRange(11, 20)])
    assert ranges_cover_file(ranges, 21) is True
    assert ranges_cover_file(ranges, 22) is False


def test_ranges_json_roundtrip():
    data = ranges_to_json([ByteRange(0, 5), ByteRange(6, 10)])
    restored = ranges_from_json(data)
    assert restored == merge_ranges([ByteRange(0, 5), ByteRange(6, 10)])


def test_parse_range_header_invalid_cases():
    assert parse_range_header(None, 100) is None
    assert parse_range_header("bytes=-0", 100) is None
    assert parse_range_header("bytes=500-100", 100) is None
    assert parse_range_header("invalid", 100) is None
    assert ByteRange(0, 9).length == 10


def test_parse_content_range_invalid():
    assert parse_content_range(None) is None
    assert parse_content_range("bad") is None
    assert parse_content_range("bytes 5-2/10") is None
    assert parse_content_range("bytes 0-9/*") == (0, 9, None)


def test_ranges_from_json_skips_bad_items():
    assert ranges_from_json([[0, 1], "bad", [2, 3]]) == merge_ranges(
        [ByteRange(0, 1), ByteRange(2, 3)]
    )

