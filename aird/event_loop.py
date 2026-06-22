"""Optional asyncio event loop tuning (Linux uvloop, free-threaded I/O pool)."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

_uvloop_installed = False
_io_pool_configured = False


def install_uvloop_if_linux() -> bool:
    """Use libuv-backed asyncio on Linux when uvloop is installed."""
    global _uvloop_installed
    if _uvloop_installed:
        return True
    if not sys.platform.startswith("linux"):
        return False
    try:
        import asyncio

        import uvloop

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        _uvloop_installed = True
        logger.info("Using uvloop event loop (Linux)")
        return True
    except ImportError:
        logger.debug("uvloop not installed; using default asyncio loop")
        return False
    except Exception:
        logger.warning("Failed to enable uvloop; using default asyncio loop", exc_info=True)
        return False


def _gil_enabled() -> bool:
    checker = getattr(sys, "_is_gil_enabled", None)
    if callable(checker):
        return bool(checker())
    return True


def apply_io_thread_pool(loop: Any = None) -> bool:
    """Enlarge the asyncio default thread pool for parallel upload disk I/O."""
    global _io_pool_configured
    if _io_pool_configured:
        return True
    try:
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        if loop is None:
            loop = asyncio.get_event_loop()

        cpus = os.cpu_count() or 4
        workers = max(8, min(32, cpus * 4))
        if sys.version_info >= (3, 13) and not _gil_enabled():
            workers = max(workers, min(64, cpus * 8))
            logger.info(
                "Free-threaded Python detected; I/O thread pool workers=%s", workers
            )
        else:
            logger.debug("I/O thread pool workers=%s", workers)

        loop.set_default_executor(ThreadPoolExecutor(max_workers=workers))
        _io_pool_configured = True
        return True
    except Exception:
        logger.warning("Failed to configure I/O thread pool", exc_info=True)
        return False
