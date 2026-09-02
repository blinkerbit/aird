"""Periodic Microsoft Graph sync of marked OneDrive files."""

from __future__ import annotations

import logging
import threading

import tornado.ioloop

logger = logging.getLogger(__name__)
_busy = threading.Lock()
_FIRST_DELAY_SEC = 45


def start_onedrive_sync_scheduler(io_loop: tornado.ioloop.IOLoop | None = None) -> None:
    loop = io_loop or tornado.ioloop.IOLoop.current()
    loop.call_later(_FIRST_DELAY_SEC, lambda: _tick(loop))


def _interval_seconds() -> int:
    try:
        import aird.constants as constants
        from aird.plugins.onedrive.settings import get_settings

        minutes = get_settings(getattr(constants, "DB_CONN", None))["sync_interval_minutes"]
        return max(5, int(minutes)) * 60
    except Exception:
        return 10 * 60


def _tick(loop: tornado.ioloop.IOLoop) -> None:
    try:
        _kick(loop)
    except Exception:
        logger.exception("OneDrive sync tick failed")
    loop.call_later(_interval_seconds(), lambda: _tick(loop))


def _kick(loop: tornado.ioloop.IOLoop) -> None:
    if not _busy.acquire(blocking=False):
        return

    def run() -> None:
        try:
            import aird.constants as constants
            from aird.plugins.onedrive.sync import sync_all_users

            sync_all_users(getattr(constants, "DB_CONN", None))
        except Exception:
            logger.exception("OneDrive periodic sync failed")
        finally:
            _busy.release()

    try:
        loop.run_in_executor(None, run)
    except Exception:
        _busy.release()
        raise
