"""Runtime status, pause flag, and failed-item queue for host sync."""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone

_lock = threading.Lock()
_paused = False
_in_progress: list[str] = []
_queue: deque[str] = deque()
_failed: deque[dict] = deque(maxlen=200)
_recent: deque[dict] = deque(maxlen=100)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_paused(value: bool) -> None:
    global _paused
    with _lock:
        _paused = bool(value)


def is_paused() -> bool:
    with _lock:
        return _paused


def enqueue_paths(paths: list[str]) -> None:
    with _lock:
        for p in paths:
            if p and p not in _queue and p not in _in_progress:
                _queue.append(p)


def pop_queue() -> str | None:
    with _lock:
        if not _queue:
            return None
        item = _queue.popleft()
        if item not in _in_progress:
            _in_progress.append(item)
        return item


def finish_path(path: str, *, ok: bool, error: str | None = None) -> None:
    with _lock:
        if path in _in_progress:
            _in_progress.remove(path)
        entry = {"path": path, "at": _now(), "ok": ok, "error": error}
        _recent.appendleft(entry)
        if not ok:
            _failed.appendleft(entry)


def clear_failed() -> None:
    with _lock:
        _failed.clear()


def retry_failed() -> list[str]:
    with _lock:
        paths = [f["path"] for f in _failed if f.get("path")]
        _failed.clear()
        for p in reversed(paths):
            if p not in _queue:
                _queue.appendleft(p)
        return paths


def public_view() -> dict:
    with _lock:
        return {
            "paused": _paused,
            "in_progress": list(_in_progress),
            "queued": list(_queue),
            "failed": list(_failed),
            "recent": list(_recent)[:30],
        }
