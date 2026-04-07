"""Security utilities for path validation and WebSocket origin checking."""

import os
import re
from urllib.parse import urlparse
from aird.constants import FEATURE_FLAGS

# Windows reserved device names that cannot be used as folder names
_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)

# Only allow alphanumeric, underscore, hyphen, dot, @ in folder names
_SAFE_FOLDER_CHAR_RE = re.compile(r"[^a-zA-Z0-9_\-\.@]")


def sanitize_username_for_folder(username: str) -> str | None:
    r"""Convert a username to a safe folder name.

    Returns None if the username cannot be sanitised into a valid folder name.

    Security measures:
    - Replaces any character not in [a-zA-Z0-9_\-.@] with underscore
    - Blocks path traversal sequences (.., /, \\)
    - Rejects Windows reserved device names (CON, PRN, etc.)
    - Strips leading dots and spaces (hidden dirs / whitespace tricks)
    - Enforces 1-64 character length on the sanitised result
    - Validates the result is a single path component (no separators)
    """
    if not isinstance(username, str) or not username.strip():
        return None

    # Strip whitespace
    name = username.strip()

    # Replace unsafe characters with underscore
    name = _SAFE_FOLDER_CHAR_RE.sub("_", name)

    # Strip leading dots and underscores to prevent hidden directories
    name = name.lstrip("._")

    # Strip trailing dots and spaces (Windows ignores them, creating ambiguity)
    name = name.rstrip(". ")

    # Block empty result
    if not name:
        return None

    # Enforce length limit
    if len(name) > 20:
        name = name[:20]

    # Block Windows reserved names (case-insensitive, with or without extension)
    stem = name.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        return None

    # Final safety: must be a single path component
    if os.sep in name or "/" in name or "\\" in name:
        return None

    # Block remaining traversal patterns
    if name in (".", "..") or ".." in name:
        return None

    return name


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
