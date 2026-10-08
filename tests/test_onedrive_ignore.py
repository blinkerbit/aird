"""Gitignore matching and untracked OneDrive map collect."""

from __future__ import annotations

from unittest.mock import patch

from aird.plugins.onedrive.backup import collect_map_entries
from aird.plugins.onedrive.ignore import IgnoreMatcher, parse_ignore_lines


def test_parse_ignore_skips_comments():
    lines = parse_ignore_lines("# hi\n*.tmp\n\nbuild/\n")
    assert lines == ["*.tmp", "build/"]


def test_ignore_star_and_dir_and_negation():
    matcher = IgnoreMatcher.from_lines(["*.log", "build/", "!keep.log"])
    assert matcher.ignored("app.log") is True
    assert matcher.ignored("keep.log") is False
    assert matcher.ignored("build", is_dir=True) is True
    assert matcher.ignored("src/app.py") is False


def test_collect_map_applies_extra_ignore(tmp_path):
    data = tmp_path / "alice" / "data"
    folder = data / "root_folder"
    folder.mkdir(parents=True)
    (folder / "keep.txt").write_text("ok", encoding="utf-8")
    (folder / "skip.tmp").write_text("no", encoding="utf-8")
    (folder / ".gitignore").write_text("*.bak\n", encoding="utf-8")
    (folder / "old.bak").write_text("no", encoding="utf-8")
    with (
        patch("aird.plugins.onedrive.backup.user_data_dir_for_username", return_value=str(data)),
        patch("aird.plugins.onedrive.backup._git_untracked_under", return_value=None),
    ):
        entries = collect_map_entries(
            "alice",
            local_path="root_folder",
            remote_path="Work/root_folder",
            ignore_extra="*.tmp",
        )
    names = {arc for _, arc in entries}
    assert "Work/root_folder/keep.txt" in names
    assert "Work/root_folder/skip.tmp" not in names
    assert "Work/root_folder/old.bak" not in names
