"""Constants package - runtime config, feature flags, and message strings."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from aird.cloud import CloudManager
from aird.constants.file_ops import (  # noqa: F401, E402
    ACCESS_DENIED,
    FILE_DELETE_DISABLED,
    FILE_OR_FOLDER_NOT_FOUND,
    FOLDER_DELETE_DISABLED,
    FOLDER_NOT_EMPTY,
)

# Will be set in main() after parsing configuration
ACCESS_TOKEN = None
ADMIN_TOKEN = None
ROOT_DIR = os.getcwd()
DB_CONN = None
DB_PATH = None
CLOUD_MANAGER = CloudManager()
CLOUD_SHARE_FOLDER = ".aird_cloud"
MULTI_USER = False

# Default feature flags (can be overridden by config.json or database)
FEATURE_FLAGS = {
    "file_upload": True,
    "file_delete": True,
    "file_rename": True,
    "file_download": True,
    "file_edit": True,
    "file_share": True,
    "compression": True,
    "super_search": True,
    "p2p_transfer": True,
    "folder_create": True,
    "folder_delete": True,
    "allow_simple_passwords": False,
    "favorites": True,
    "storage_quotas": False,
    "abac_engine": False,
    "abac_audit_decisions": True,
    "email_notifications": False,
}

# WebSocket connection configuration
WEBSOCKET_CONFIG = {
    "feature_flags_max_connections": 50,
    "feature_flags_idle_timeout": 600,  # 10 minutes
    "file_streaming_max_connections": 200,
    "file_streaming_idle_timeout": 300,  # 5 minutes
    "search_max_connections": 100,
    "search_idle_timeout": 180,  # 3 minutes
}

# Upload configuration (admin-configurable, persisted to database)
UPLOAD_CONFIG = {
    "max_file_size_mb": 512,  # Default max upload file size in MB
    "allow_all_file_types": 0,  # 0 = use whitelist below, 1 = allow any extension
}

# File operation constants (derived from UPLOAD_CONFIG at startup)
MAX_FILE_SIZE = UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
# HTTP /upload body limit (browser uploads stream POST /upload; CLI may POST whole file)
UPLOAD_REQUEST_MAX_BODY_SIZE = MAX_FILE_SIZE + (1024 * 1024)
MAX_READABLE_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# Default line window for /files/... viewer when no ?end_line= is supplied (protects DOM from huge renders)
DEFAULT_FILE_VIEW_LINE_LIMIT = 1000
# Default whitelist for uploads; also used as the list of options in admin when "allow all" is off
ALLOWED_UPLOAD_EXTENSIONS = {
    ".txt",
    ".log",
    ".md",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".csv",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".ico",
    ".mp4",
    ".webm",
    ".ogg",
    ".mp3",
    ".wav",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".bz2",
}
# Runtime set of allowed extensions (loaded from DB; used when allow_all_file_types is off)
UPLOAD_ALLOWED_EXTENSIONS = set(ALLOWED_UPLOAD_EXTENSIONS)

# Mmap constants
MMAP_MIN_SIZE = 1 * 1024 * 1024  # 1 MB
CHUNK_SIZE = 64 * 1024  # 64 KB
# WebSocket binary frame size (stay under Cloudflare ~1 MiB message limit)
WS_TRANSFER_FRAME_BYTES = 768 * 1024

# Network share manager (set at startup)
NETWORK_SHARE_MANAGER = None

# Rate limiting
LOGIN_RATE_LIMIT_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW = 300  # 5 minutes

# ABAC environment: comma-separated CIDR blocks treated as "corporate" IPs.
# Admins can override this via the AIRD_CORPORATE_IP_CIDRS env var at startup.
# Example: "10.0.0.0/8,192.168.0.0/16"
CORPORATE_IP_CIDRS: list[str] = [
    c.strip()
    for c in os.environ.get("AIRD_CORPORATE_IP_CIDRS", "").split(",")
    if c.strip()
]


def _read_app_version() -> str:
    """Package version for cache-busting static assets in templates (?v=…)."""
    try:
        from importlib.metadata import version

        return version("aird")
    except Exception:
        pass
    try:
        import re
        from pathlib import Path

        setup_py = Path(__file__).resolve().parents[2] / "setup.py"
        text = setup_py.read_text(encoding="utf-8")
        match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "dev"


APP_VERSION = _read_app_version()

_UI_PACKAGE_SUFFIXES = frozenset(
    {".html", ".css", ".js", ".png", ".ico", ".svg", ".jpg", ".jpeg", ".webp", ".gif"}
)
_PKG_ROOT = Path(__file__).resolve().parent.parent
_static_version_cache: tuple[float, str] | None = None
_STATIC_VERSION_TTL = 2.0


def _ui_fingerprint() -> str:
    """Hex fingerprint from latest mtime among shipped UI package-data files."""
    latest = 0
    for sub in ("static", "templates"):
        base = _PKG_ROOT / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _UI_PACKAGE_SUFFIXES:
                continue
            try:
                latest = max(latest, path.stat().st_mtime_ns)
            except OSError:
                pass
    return format(latest, "x")


def get_static_version() -> str:
    """Cache-bust query value: package version plus UI file fingerprint."""
    import time

    global _static_version_cache
    now = time.monotonic()
    if _static_version_cache and now - _static_version_cache[0] < _STATIC_VERSION_TTL:
        return _static_version_cache[1]
    version = f"{APP_VERSION}-{_ui_fingerprint()}"
    _static_version_cache = (now, version)
    return version
