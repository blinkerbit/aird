"""Dedicated Rust HTTP upload listener (socket → disk, no Python body hop)."""

from __future__ import annotations

import logging
import os
import secrets
import threading
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_port: int = 0
_started = False


def transfer_upload_port() -> int:
    env = os.environ.get("AIRD_TRANSFER_PORT", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return _port


def native_upload_enabled() -> bool:
    return _started and transfer_upload_port() > 0


def mint_ticket() -> str:
    return secrets.token_hex(16)


def start_native_upload_listener(http_port: int) -> int:
    """Bind Rust upload HTTP on AIRD_TRANSFER_PORT or http_port+1. Returns bound port or 0."""
    global _port, _started
    with _lock:
        if _started:
            return _port
        try:
            import aird_transfer as native  # type: ignore[import-not-found]
        except ImportError:
            logger.info("aird_transfer missing — uploads stay on socketify on_data")
            return 0
        if not hasattr(native, "start_upload_http_server"):
            logger.warning("aird_transfer built without upload HTTP server")
            return 0

        port = int(os.environ.get("AIRD_TRANSFER_PORT", "0") or "0")
        if port <= 0:
            port = int(http_port) + 1
        host = os.environ.get("AIRD_TRANSFER_HOST", "0.0.0.0") or "0.0.0.0"
        try:
            bound = int(native.start_upload_http_server(host, port))
        except Exception:
            logger.exception("Failed to start native upload HTTP on %s:%s", host, port)
            return 0
        _port = bound
        _started = True
        logger.info(
            "Native zero-copy upload HTTP on http://%s:%s (set AIRD_TRANSFER_PORT to override)",
            host,
            bound,
        )
        return bound


def register_session(session_id: str, ticket: str, file_fd: int, total: int) -> bool:
    try:
        import aird_transfer as native  # type: ignore[import-not-found]
    except ImportError:
        return False
    if not hasattr(native, "register_upload_session"):
        return False
    try:
        native.register_upload_session(session_id, ticket, int(file_fd), int(total))
        return True
    except Exception:
        logger.exception("register_upload_session failed")
        return False


def unregister_session(session_id: str) -> None:
    try:
        import aird_transfer as native  # type: ignore[import-not-found]
    except ImportError:
        return
    if not hasattr(native, "unregister_upload_session"):
        return
    try:
        native.unregister_upload_session(session_id)
    except Exception:
        logger.debug("unregister_upload_session failed", exc_info=True)


def strategy_fields() -> dict[str, Any]:
    """Extra keys for get_effective_transfer_strategy / browse config."""
    if not native_upload_enabled():
        return {"nativeUploadPort": 0, "nativeUploadEnabled": False}
    return {
        "nativeUploadPort": transfer_upload_port(),
        "nativeUploadEnabled": True,
    }
