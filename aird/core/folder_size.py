"""Incremental folder size calculation (sum of file sizes)."""

from __future__ import annotations

import os


# Files processed per batch before yielding back to the event loop.
FOLDER_SIZE_BATCH_FILES = 250


class FolderSizeWalker:
    """Walk a directory tree in batches without loading all paths at once."""

    def __init__(self, root_abspath: str) -> None:
        self._walk_gen = os.walk(root_abspath)
        self._current_dir: str | None = None
        self._pending_files: list[str] = []
        self.total_bytes = 0
        self.file_count = 0
        self.done = False

    def _refill_pending_files(self) -> bool:
        """Load the next directory's filenames. Returns False when walk is exhausted."""
        try:
            self._current_dir, _dirnames, filenames = next(self._walk_gen)
            self._pending_files = list(filenames)
            return True
        except StopIteration:
            self.done = True
            return False

    def _account_file(self, fname: str) -> None:
        if not self._current_dir:
            return
        fpath = os.path.join(self._current_dir, fname)
        try:
            if os.path.isfile(fpath):
                self.total_bytes += os.path.getsize(fpath)
        except OSError:
            pass
        self.file_count += 1

    def step(self, batch_size: int = FOLDER_SIZE_BATCH_FILES) -> tuple[int, int, bool]:
        """Process up to *batch_size* files. Returns (total_bytes, file_count, done)."""
        if self.done:
            return self.total_bytes, self.file_count, True

        processed = 0
        while processed < batch_size and not self.done:
            if not self._pending_files and not self._refill_pending_files():
                break

            while self._pending_files and processed < batch_size:
                self._account_file(self._pending_files.pop())
                processed += 1

        return self.total_bytes, self.file_count, self.done


def norm_rel_path(rel_path: str) -> str:
    return rel_path.replace("\\", "/").strip().strip("/")


def resolve_folder_abspath(user_root: str, rel_path: str) -> str | None:
    """Return absolute folder path or None if invalid / not a directory."""
    from aird.core.security import is_within_root

    rel = norm_rel_path(rel_path)
    if not rel or ".." in rel.split("/"):
        return None
    abs_path = os.path.abspath(os.path.join(user_root, rel))
    if not is_within_root(abs_path, user_root) or not os.path.isdir(abs_path):
        return None
    return abs_path


def compute_folder_size(root_abspath: str) -> tuple[int, int]:
    """Sum file sizes under *root_abspath*. Returns (total_bytes, file_count)."""
    walker = FolderSizeWalker(root_abspath)
    while not walker.done:
        walker.step(FOLDER_SIZE_BATCH_FILES)
    return walker.total_bytes, walker.file_count
