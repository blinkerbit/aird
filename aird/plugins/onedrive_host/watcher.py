"""Filesystem watchers with debounced sync queue."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_observer = None
_timer: threading.Timer | None = None
_debounce_sec = 45


def _schedule_kick() -> None:
    global _timer
    if _timer:
        _timer.cancel()

    def run() -> None:
        from aird.plugins.onedrive_host.sync_queue import kick_sync

        try:
            kick_sync()
        except Exception:
            logger.exception("onedrive host debounced sync failed")

    _timer = threading.Timer(_debounce_sec, run)
    _timer.daemon = True
    _timer.start()


def _on_fs_event(_event) -> None:
    from aird.plugins.onedrive_host import status as st

    if st.is_paused():
        return
    _schedule_kick()


def start_watchers(paths: list[str]) -> None:
    global _observer
    stop_watchers()
    cleaned = {p for p in paths if p}
    if not cleaned:
        return
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        logger.warning("watchdog not installed; host sync uses interval fallback only")
        return

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            if event.is_directory:
                return
            _on_fs_event(event)

    _observer = Observer()
    handler = Handler()
    for path in cleaned:
        try:
            _observer.schedule(handler, path, recursive=True)
        except OSError as exc:
            logger.warning("Could not watch %s: %s", path, exc)
    _observer.daemon = True
    _observer.start()
    logger.info("OneDrive host watching %d path(s)", len(cleaned))


def stop_watchers() -> None:
    global _observer
    if _observer:
        try:
            _observer.stop()
            _observer.join(timeout=5)
        except Exception:
            logger.debug("stop watchers", exc_info=True)
        _observer = None


def reload_watchers(conn) -> None:
    from aird.plugins.onedrive_host.sync import mapped_local_paths

    paths = mapped_local_paths(conn)
    start_watchers(paths)
