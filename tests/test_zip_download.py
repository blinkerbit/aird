import os

import pytest

from aird.core.zip_download import (
    ZipDownloadError,
    build_zip_file,
    collect_zip_entries,
)
import zipfile


def test_collect_zip_entries_file_and_folder(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world", encoding="utf-8")

    entries = collect_zip_entries(str(root), ["a.txt", "sub"])
    arcs = {arc for _abs, arc in entries}
    assert "a.txt" in arcs
    assert "sub/b.txt" in arcs

    zip_path = build_zip_file(entries)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        assert "a.txt" in names
        assert "sub/b.txt" in names
    finally:
        os.remove(zip_path)


def test_collect_rejects_path_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ZipDownloadError) as exc:
        collect_zip_entries(str(root), ["../outside.txt"])
    assert exc.value.status == 403
