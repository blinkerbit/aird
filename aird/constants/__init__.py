"""Constants package - runtime config, feature flags, and message strings."""

import os
import sys
import threading
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
    "webauthn": False,
    "smb_server": False,
    "webdav_server": False,
    "transfer_sendfile": True,
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
    "max_file_size_mb": 10240,  # Default max upload file size in MB (10 GB)
    "allow_all_file_types": 0,  # 0 = use whitelist below, 1 = allow any extension
}

# LAN / WireGuard: single-stream HTTP POST/GET only.
TRANSFER_CONFIG_REVISION = 0
UPLOAD_CONCURRENCY = 2


def get_effective_transfer_strategy() -> dict:
    """Return browser/server transfer settings (stream upload/download only)."""
    max_mb = max(1, int(UPLOAD_CONFIG.get("max_file_size_mb", 10240)))
    return {
        "revision": TRANSFER_CONFIG_REVISION,
        "maxFileSize": max_mb * 1024 * 1024,
        "directUploadMaxBytes": max_mb * 1024 * 1024,
        "bulkWsPath": "/ws/bulk",
        "bulkTcpPort": int(os.environ.get("AIRD_BULK_PORT", "0") or "0"),
        "uploadConcurrency": UPLOAD_CONCURRENCY,
    }


def bump_transfer_config_revision() -> int:
    global TRANSFER_CONFIG_REVISION
    TRANSFER_CONFIG_REVISION += 1
    refresh_upload_derived_constants()
    return TRANSFER_CONFIG_REVISION

# File operation constants (derived from UPLOAD_CONFIG; call refresh_upload_derived_constants after changes)
MAX_FILE_SIZE = UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
UPLOAD_REQUEST_MAX_BODY_SIZE = MAX_FILE_SIZE + (1024 * 1024)
LARGE_FILE_THRESHOLD_BYTES = MAX_FILE_SIZE + 1

COMPRESSION_CONFIG = {
    "mode": "wan_only",
    "level": 6,
    "algorithms": ["gzip"],
    "min_bytes": 1024,
    "max_bytes": 50 * 1024 * 1024,
}

TRANSFER_CONFIG = {
    "upload_mb_per_sec": 0,
    "download_mb_per_sec": 0,
    "burst_mb": 64,
    "max_concurrent": 0,
}


_RUNTIME_CONFIG_LOCK = threading.RLock()


def _refresh_upload_derived_constants_impl() -> None:
    global MAX_FILE_SIZE, UPLOAD_REQUEST_MAX_BODY_SIZE, LARGE_FILE_THRESHOLD_BYTES
    MAX_FILE_SIZE = UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
    LARGE_FILE_THRESHOLD_BYTES = MAX_FILE_SIZE + 1
    UPLOAD_REQUEST_MAX_BODY_SIZE = MAX_FILE_SIZE + (1024 * 1024)


def refresh_upload_derived_constants() -> None:
    """Recompute upload size limits after UPLOAD_CONFIG is loaded or changed."""
    with _RUNTIME_CONFIG_LOCK:
        _refresh_upload_derived_constants_impl()


def merge_persisted_upload_config(persisted_upload: dict | None) -> None:
    """Apply upload settings from DB under the runtime config lock."""
    with _RUNTIME_CONFIG_LOCK:
        if persisted_upload:
            for key, value in persisted_upload.items():
                if key in UPLOAD_CONFIG:
                    UPLOAD_CONFIG[key] = int(value)
        _refresh_upload_derived_constants_impl()


refresh_upload_derived_constants()
# Max JSON WebSocket control message size (search, stream commands, P2P signaling)
WS_JSON_MESSAGE_MAX_BYTES = 64 * 1024
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
# Legacy WebSocket frame size (optional fallback; HTTP is primary for transfers)
WS_TRANSFER_FRAME_BYTES = 2 * 1024 * 1024

# Network share manager (set at startup)
NETWORK_SHARE_MANAGER = None

# Rate limiting
LOGIN_RATE_LIMIT_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW = 300  # 5 minutes

# Server-side login sessions
SESSION_IDLE_TIMEOUT_SECONDS = 3 * 60 * 60  # 3 hours
SESSION_MAX_AGE_SECONDS = 24 * 60 * 60  # 1 day absolute cap
SESSION_TOUCH_INTERVAL_SECONDS = 60

# ABAC environment: comma-separated CIDR blocks treated as "corporate" IPs.
# Admins can override this via the AIRD_CORPORATE_IP_CIDRS env var at startup.
# Example: "10.0.0.0/8,192.168.0.0/16"
CORPORATE_IP_CIDRS: list[str] = [
    c.strip()
    for c in os.environ.get("AIRD_CORPORATE_IP_CIDRS", "").split(",")
    if c.strip()
]


def _read_app_version() -> str:
    """Package version string (from installed metadata or setup.py)."""
    try:
        from importlib.metadata import version

        return version("aird")
    except Exception:
        pass
    try:
        import re

        setup_py = Path(__file__).resolve().parents[2] / "setup.py"
        text = setup_py.read_text(encoding="utf-8")
        match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "dev"


APP_VERSION = _read_app_version()


def get_static_version() -> str:
    """Return app version. Static assets use Cache-Control (no query-string busting)."""
    return APP_VERSION
