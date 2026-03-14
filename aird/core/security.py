"""Security utilities for path validation and WebSocket origin checking."""

import os
import re
from urllib.parse import urlparse
from aird.constants import FEATURE_FLAGS


def join_path(*parts):
    """Join path parts and normalize separators."""
    return os.path.join(*parts).replace("\\", "/")


def validate_password(password: str) -> tuple[bool, str]:
    """
    Validate password strength.
    Returns (is_valid, error_message)
    Requires:
    - Minimum 12 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one number
    - At least one special character
    """
    if FEATURE_FLAGS.get("allow_simple_passwords", False):
        return True, ""

    if len(password) < 12:
        return False, "Password must be at least 12 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain at least one special character."
    return True, ""


def is_within_root(path: str, root: str) -> bool:
    """Return True if path is within root after resolving symlinks and normalization."""
    try:
        path_real = os.path.realpath(path)
        root_real = os.path.realpath(root)
        return os.path.commonpath([path_real, root_real]) == root_real
    except Exception:
        return False


def _origin_scheme_ok(origin_scheme: str, expected_scheme: str) -> bool:
    """Return True if origin scheme matches expected (http/https or ws/wss)."""
    if origin_scheme in (expected_scheme, expected_scheme + "s"):
        return True
    return origin_scheme in ("ws", "wss") and expected_scheme in ("http", "https")


def _origin_host_port_ok(
    handler, origin_host: str, origin_port: int | None, req_host: str, req_port: int
) -> bool:
    """Return True if origin host/port are acceptable."""
    allow_dev = bool(handler.settings.get("allow_dev_origins", False))
    dev_ok = allow_dev and origin_host in ("localhost", "127.0.0.1")
    if origin_host != req_host and not dev_ok:
        return False
    if origin_port is not None and origin_port != req_port and not dev_ok:
        return False
    return True


def is_valid_websocket_origin(handler, origin: str) -> bool:
    """Validate WebSocket origin matches expected host/port."""
    try:
        if not origin:
            return False
        parsed = urlparse(origin)
        origin_host = parsed.hostname
        origin_port = parsed.port
        origin_scheme = parsed.scheme
        req_host = handler.request.host.split(":")[0]
        try:
            req_port = int(handler.request.host.split(":")[1])
        except (IndexError, ValueError):
            req_port = 443 if handler.request.protocol == "https" else 80
        expected_scheme = "https" if handler.request.protocol == "https" else "http"
        if not _origin_scheme_ok(origin_scheme, expected_scheme):
            return False
        return _origin_host_port_ok(
            handler, origin_host, origin_port, req_host, req_port
        )
    except Exception:
        return False
