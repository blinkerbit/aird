"""Optional asyncio event loop tuning (Linux uvloop)."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_uvloop_installed = False


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
