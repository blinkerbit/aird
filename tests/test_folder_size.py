"""Tests for incremental folder size scanning."""

import os

import pytest

from aird.core.folder_size import FolderSizeWalker


@pytest.fixture
def sample_tree(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "f1.txt").write_bytes(b"x" * 100)
    (root / "a" / "f2.txt").write_bytes(b"y" * 50)
    sub = root / "a" / "nested"
    sub.mkdir()
    (sub / "f3.txt").write_bytes(b"z" * 25)
    return str(root / "a")


def test_folder_size_walker_totals(sample_tree):
    walker = FolderSizeWalker(sample_tree)
    while not walker.done:
        walker.step(batch_size=1)
    assert walker.file_count == 3
    assert walker.total_bytes == 175


def test_folder_size_walker_batches(sample_tree):
    walker = FolderSizeWalker(sample_tree)
    total, count, done = walker.step(batch_size=2)
    assert done is False
    assert count == 2
    assert total > 0
    total2, count2, done2 = walker.step(batch_size=10)
    assert done2 is True
    assert count2 == 3
    assert total2 == 175
