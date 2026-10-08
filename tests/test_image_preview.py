"""Tests for embedded JPEG previews in camera RAW files."""

from aird.core.image_preview import embedded_jpeg_preview, inline_preview_payload


def _fake_jpeg(n: int) -> bytes:
    return b"\xff\xd8\xff\xe0" + (b"A" * n) + b"\xff\xd9"


def test_picks_largest_embedded_jpeg(tmp_path):
    small = _fake_jpeg(100)
    big = _fake_jpeg(8000)
    path = tmp_path / "shot.dng"
    path.write_bytes(b"DNGHDR" + small + b"pad" + big + b"TAIL")
    got = embedded_jpeg_preview(str(path))
    assert got == big
    payload = inline_preview_payload(str(path))
    assert payload == (big, "image/jpeg")


def test_jpeg_file_has_no_raw_preview(tmp_path):
    path = tmp_path / "shot.jpg"
    path.write_bytes(_fake_jpeg(8000))
    assert inline_preview_payload(str(path)) is None


def test_missing_jpeg_returns_none(tmp_path):
    path = tmp_path / "empty.dng"
    path.write_bytes(b"not an image at all" * 50)
    assert embedded_jpeg_preview(str(path)) is None
    assert inline_preview_payload(str(path)) is None
