"""Security utilities for path validation and WebSocket origin checking."""

import os
from urllib.parse import urlparse


def join_path(*parts):
    """Join path parts and normalize separators."""
    return os.path.join(*parts).replace("\\", "/")


def is_within_root(path: str, root: str) -> bool:
    """Return True if path is within root after resolving symlinks and normalization."""
    try:
        path_real = os.path.realpath(path)
        root_real = os.path.realpath(root)
        return os.path.commonpath([path_real, root_real]) == root_real
    except Exception:
        return False


def is_valid_websocket_origin(handler, origin: str) -> bool:
    """Validate WebSocket origin matches expected host/port."""
    try:
        if not origin:
            return False
        parsed = urlparse(origin)
        origin_host = parsed.hostname
        origin_port = parsed.port
        origin_scheme = parsed.scheme
        # Determine expected host/port from request
        req_host = handler.request.host.split(":")[0]
        try:
            req_port = int(handler.request.host.split(":")[1])
        except (IndexError, ValueError):
            req_port = 443 if handler.request.protocol == "https" else 80
        expected_scheme = "https" if handler.request.protocol == "https" else "http"
        if origin_scheme not in (expected_scheme, expected_scheme + "s"):
            # Allow ws/wss equivalents to http/https
            if not (origin_scheme in ("ws", "wss") and expected_scheme in ("http", "https")):
                return False
        if origin_host != req_host:
            # Allow localhost in development if explicitly enabled
            allow_dev = bool(handler.settings.get("allow_dev_origins", False))
            if not (allow_dev and origin_host in {"localhost", "127.0.0.1"}):
                return False
        if origin_port and origin_port != req_port:
            # Different port -> reject unless allow_dev and localhost
            allow_dev = bool(handler.settings.get("allow_dev_origins", False))
            if not (allow_dev and origin_host in {"localhost", "127.0.0.1"}):
                return False
        return True
    except Exception:
        return False
