"""Background sync worker for host OneDrive."""

from __future__ import annotations

import logging
import threading

import tornado.ioloop

from aird.plugins.onedrive_host import status as host_status

logger = logging.getLogger(__name__)
_busy = threading.Lock()
_FIRST_DELAY_SEC = 60


def kick_sync() -> None:
    if host_status.is_paused():
        return
    if not _busy.acquire(blocking=False):
        return
    loop = tornado.ioloop.IOLoop.current()

    def run() -> None:
        try:
            import aird.constants as constants
            from aird.plugins.onedrive_host.sync import sync_host

            sync_host(getattr(constants, "DB_CONN", None))
        except Exception:
            logger.exception("onedrive host sync failed")
        finally:
            _busy.release()

    try:
        loop.run_in_executor(None, run)
    except Exception:
        _busy.release()
        raise


def start_host_scheduler(io_loop=None) -> None:
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


def _tick(loop) -> None:
    try:
        kick_sync()
    except Exception:
        logger.exception("onedrive host tick failed")
    loop.call_later(_interval_seconds(), lambda: _tick(loop))
