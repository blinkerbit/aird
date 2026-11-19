"""Security utilities for path validation and WebSocket origin checking."""

import hashlib
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

    # Long names: append hash suffix to avoid truncation collisions
    if len(name) > 20:
        digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:12]
        name = name[:20] + "_" + digest
    elif len(name) > 64:
        name = name[:64]

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


def legacy_folder_name(username: str) -> str | None:
    """Pre-hash folder names: sanitize then truncate to 20 chars."""
    if not isinstance(username, str) or not username.strip():
        return None
    name = username.strip()
    name = _SAFE_FOLDER_CHAR_RE.sub("_", name)
    name = name.lstrip("._").rstrip(". ")
    if not name:
        return None
    if len(name) > 20:
        name = name[:20]
    stem = name.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        return None
    if os.sep in name or "/" in name or "\\" in name:
        return None
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


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def client_ip(peer: str | None, forwarded: str | None) -> str:
    """Use the proxy-appended address when the immediate peer is loopback.

    A client-supplied X-Forwarded-For is ignored unless the connection itself
    came from localhost, which is the reverse proxy. The last hop is the one
    the proxy added.
    """
    host = (peer or "").strip()
    if host.startswith("::ffff:"):
        host = host[7:]
    header = (forwarded or "").strip()
    if header and host in _LOOPBACK_HOSTS:
        parts = [part.strip() for part in header.split(",") if part.strip()]
        if parts:
            return parts[-1]
    return host


def collapse_rel(rel: str) -> str | None:
    """Collapse ``.`` and ``..`` lexically.

    Returns ``""`` for the root itself. Returns ``None`` when the path escapes
    above the root or contains a NUL.
    """
    raw = (rel or "").replace("\\", "/").strip()
    if "\x00" in raw:
        return None
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


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


def _request_headers(handler):
    req = handler.request
    return req.headers if hasattr(req, "headers") else {}


def _header_first(headers, name: str) -> str:
    raw = headers.get(name) or headers.get(name.lower()) or ""
    return raw.split(",")[0].strip()


def _peer_is_loopback(handler) -> bool:
    req = handler.request
    peer = ""
    if getattr(req, "remote_ip", None):
        peer = str(req.remote_ip)
    else:
        client = getattr(req, "client", None)
        if client is not None:
            peer = getattr(client, "host", "") or ""
    if peer.startswith("::ffff:"):
        peer = peer[7:]
    return peer in _LOOPBACK_HOSTS


def effective_request_endpoint(handler) -> tuple[str, int, str]:
    """Public host, port, and scheme (http/https) for origin and WebAuthn RP."""
    headers = _request_headers(handler)
    if _peer_is_loopback(handler):
        fwd_host = _header_first(headers, "X-Forwarded-Host")
        if fwd_host:
            req_host = fwd_host.split(":")[0]
            try:
                req_port = int(fwd_host.split(":")[1])
            except (IndexError, ValueError):
                fwd_proto = _header_first(headers, "X-Forwarded-Proto")
                req_port = 443 if fwd_proto == "https" else 80
            fwd_proto = _header_first(headers, "X-Forwarded-Proto")
            protocol = fwd_proto or ("https" if req_port == 443 else "http")
            return req_host, req_port, protocol

    req = handler.request
    host_str = ""
    if getattr(req, "host", None):
        host_str = str(req.host)
    if not host_str:
        host_str = _header_first(headers, "host") or getattr(
            getattr(req, "url", None), "netloc", ""
        )
    if not host_str:
        host_str = "localhost"
    req_host = host_str.split(":")[0]
    try:
        req_port = int(host_str.split(":")[1])
    except (IndexError, ValueError):
        protocol = getattr(req, "protocol", None) or _header_first(
            headers, "X-Forwarded-Proto"
        )
        req_port = 443 if protocol == "https" else 80
    protocol = getattr(req, "protocol", None) or _header_first(
        headers, "X-Forwarded-Proto"
    )
    if not protocol:
        protocol = "https" if req_port == 443 else "http"
    return req_host, req_port, protocol


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
        req_host, req_port, req_protocol = effective_request_endpoint(handler)
        expected_scheme = req_protocol if req_protocol in ("http", "https") else "http"
        if not _origin_scheme_ok(origin_scheme, expected_scheme):
            return False
        return _origin_host_port_ok(
            handler, origin_host, origin_port, req_host, req_port
        )
    except Exception:
        return False
