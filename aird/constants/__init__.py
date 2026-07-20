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
    # Existing Open-profile defaults; profile resolution applies stricter caps.
    "single_request_max_mb": 100,
    # HTTP parallel upload: chunk size (MB) and concurrent streams.
    # Peak server RAM per active upload ≈ range_chunk_mb × range_upload_concurrency.
    "range_chunk_mb": 90,
    "range_upload_concurrency": 16,
    # WebSocket upload chunk size (MB). Optional fallback path.
    "ws_chunk_mb": 90,
}

# Mutually-exclusive deployment profile. This is not a feature-flag: only one
# transport strategy can be active at a time.
TRANSFER_PROFILE_NAMES = ("cloudflare", "wireguard", "open")
TRANSFER_PROFILE_PRESETS = {
    "cloudflare": {
        "upload_transport": "ranged",
        "download_transport": "ranged",
        "single_request_max_mb": 90,
        "range_chunk_mb": 90,
        "range_upload_concurrency": 8,
        "range_download_concurrency": 8,
        "range_pipeline_depth": 1,
    },
    "wireguard": {
        "upload_transport": "stream",
        "download_transport": "stream",
        "single_request_max_mb": None,  # resolved to max_file_size_mb
        "range_chunk_mb": 90,
        "range_upload_concurrency": 1,
        "range_download_concurrency": 1,
        "range_pipeline_depth": 1,
    },
    "open": {
        "upload_transport": "adaptive",
        "download_transport": "adaptive",
        "single_request_max_mb": 64,
        "range_chunk_mb": 32,
        "range_upload_concurrency": 8,
        "range_download_concurrency": 8,
        "range_pipeline_depth": 2,
    },
}
TRANSFER_PROFILE = "open"
TRANSFER_CONFIG_REVISION = 0


def normalize_transfer_profile(value: object, default: str = "open") -> str:
    profile = str(value or "").strip().lower()
    return profile if profile in TRANSFER_PROFILE_NAMES else default


def transfer_profile_env_override() -> str | None:
    raw = os.environ.get("AIRD_TRANSFER_PROFILE", "").strip().lower()
    return raw if raw in TRANSFER_PROFILE_NAMES else None


def set_transfer_profile(profile: object, revision: int | None = None) -> str:
    """Apply the effective profile; a valid environment override always wins."""
    global TRANSFER_PROFILE, TRANSFER_CONFIG_REVISION
    TRANSFER_PROFILE = transfer_profile_env_override() or normalize_transfer_profile(profile)
    if revision is not None:
        TRANSFER_CONFIG_REVISION = max(0, int(revision))
    refresh = globals().get("refresh_upload_derived_constants")
    if callable(refresh):
        refresh()
    return TRANSFER_PROFILE


def apply_transfer_profile_defaults(profile: object) -> str:
    """Reset transport tuning to the selected profile's safe defaults."""
    normalized = normalize_transfer_profile(profile)
    preset = TRANSFER_PROFILE_PRESETS[normalized]
    max_mb = max(1, int(UPLOAD_CONFIG.get("max_file_size_mb", 10240)))
    direct_mb = preset["single_request_max_mb"]
    UPLOAD_CONFIG["single_request_max_mb"] = (
        max_mb if direct_mb is None else min(max_mb, int(direct_mb))
    )
    UPLOAD_CONFIG["range_chunk_mb"] = int(preset["range_chunk_mb"])
    UPLOAD_CONFIG["range_upload_concurrency"] = int(
        preset["range_upload_concurrency"]
    )
    return normalized


def get_effective_transfer_strategy() -> dict:
    """Return a fresh browser/server strategy snapshot for new transfers."""
    profile = TRANSFER_PROFILE
    preset = TRANSFER_PROFILE_PRESETS[profile]
    max_mb = max(1, int(UPLOAD_CONFIG.get("max_file_size_mb", 10240)))
    if profile == "wireguard":
        direct_mb = max_mb
    elif profile == "cloudflare":
        direct_mb = min(max_mb, 90)
    else:
        configured_direct_mb = int(
            UPLOAD_CONFIG.get("single_request_max_mb", 0) or 0
        )
        direct_mb = min(
            max_mb,
            configured_direct_mb
            if configured_direct_mb > 0
            else _DEFAULT_PARALLEL_THRESHOLD_MB,
        )

    if profile == "cloudflare":
        chunk_mb = min(90, max(4, int(UPLOAD_CONFIG.get("range_chunk_mb", 90) or 90)))
        upload_concurrency = min(
            8, max(1, int(UPLOAD_CONFIG.get("range_upload_concurrency", 8) or 8))
        )
    elif profile == "open":
        chunk_mb = max(4, min(200, int(UPLOAD_CONFIG.get("range_chunk_mb", 32) or 32)))
        upload_concurrency = max(
            1, min(16, int(UPLOAD_CONFIG.get("range_upload_concurrency", 8) or 8))
        )
    else:
        chunk_mb = int(preset["range_chunk_mb"])
        upload_concurrency = 1

    return {
        "profile": profile,
        "revision": TRANSFER_CONFIG_REVISION,
        "uploadTransport": preset["upload_transport"],
        "downloadTransport": preset["download_transport"],
        "maxFileSize": max_mb * 1024 * 1024,
        "directUploadMaxBytes": direct_mb * 1024 * 1024,
        "rangeChunkBytes": chunk_mb * 1024 * 1024,
        "rangeUploadConcurrency": upload_concurrency,
        "rangeDownloadConcurrency": int(preset["range_download_concurrency"]),
        "rangePipelineDepth": int(preset["range_pipeline_depth"]),
    }

# File operation constants (derived from UPLOAD_CONFIG; call refresh_upload_derived_constants after changes)
MAX_FILE_SIZE = UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
UPLOAD_REQUEST_MAX_BODY_SIZE = MAX_FILE_SIZE + (1024 * 1024)
LARGE_FILE_THRESHOLD_BYTES = MAX_FILE_SIZE
RANGE_CHUNK_BYTES = 90 * 1024 * 1024  # from range_chunk_mb via refresh_upload_derived_constants()
RANGE_UPLOAD_CONCURRENCY = 16
RANGE_DOWNLOAD_CONCURRENCY = 12
RANGE_PIPELINE_DEPTH = 2
WS_CHUNK_BYTES = 90 * 1024 * 1024  # from ws_chunk_mb via refresh_upload_derived_constants()

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


# Default parallel threshold when single_request_max_mb is 0 (proxy-safe).
_DEFAULT_PARALLEL_THRESHOLD_MB = 100


def _refresh_upload_derived_constants_impl() -> None:
    global MAX_FILE_SIZE, UPLOAD_REQUEST_MAX_BODY_SIZE, LARGE_FILE_THRESHOLD_BYTES
    global WS_CHUNK_BYTES, RANGE_CHUNK_BYTES, RANGE_UPLOAD_CONCURRENCY
    global RANGE_DOWNLOAD_CONCURRENCY, RANGE_PIPELINE_DEPTH
    MAX_FILE_SIZE = UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
    strategy = get_effective_transfer_strategy()
    LARGE_FILE_THRESHOLD_BYTES = int(strategy["directUploadMaxBytes"])
    # WireGuard streams files equal to the max size too; client routing also
    # checks uploadTransport explicitly.
    if strategy["uploadTransport"] == "stream":
        LARGE_FILE_THRESHOLD_BYTES = MAX_FILE_SIZE + 1
    single_request_bytes = min(MAX_FILE_SIZE, int(strategy["directUploadMaxBytes"]))
    RANGE_CHUNK_BYTES = int(strategy["rangeChunkBytes"])
    RANGE_UPLOAD_CONCURRENCY = int(strategy["rangeUploadConcurrency"])
    RANGE_DOWNLOAD_CONCURRENCY = int(strategy["rangeDownloadConcurrency"])
    RANGE_PIPELINE_DEPTH = int(strategy["rangePipelineDepth"])
    ws_mb = int(UPLOAD_CONFIG.get("ws_chunk_mb", 90) or 90)
    ws_mb = max(1, min(ws_mb, 200))
    WS_CHUNK_BYTES = ws_mb * 1024 * 1024
    chunk_body_bytes = RANGE_CHUNK_BYTES + (2 * 1024 * 1024)
    UPLOAD_REQUEST_MAX_BODY_SIZE = max(single_request_bytes, chunk_body_bytes) + (
        1024 * 1024
    )


def refresh_upload_derived_constants() -> None:
    """Recompute upload size limits after UPLOAD_CONFIG is loaded or changed."""
    with _RUNTIME_CONFIG_LOCK:
        _refresh_upload_derived_constants_impl()


def merge_persisted_upload_config(persisted_upload: dict | None) -> None:
    """Apply upload settings from DB under the runtime config lock."""
    with _RUNTIME_CONFIG_LOCK:
        if persisted_upload:
            for key, value in persisted_upload.items():
                UPLOAD_CONFIG[key] = int(value)
        if "single_request_max_mb" not in (persisted_upload or {}):
            UPLOAD_CONFIG["single_request_max_mb"] = 100
        if "range_chunk_mb" not in (persisted_upload or {}):
            UPLOAD_CONFIG.setdefault("range_chunk_mb", 90)
        if "range_upload_concurrency" not in (persisted_upload or {}):
            UPLOAD_CONFIG.setdefault("range_upload_concurrency", 16)
        if "ws_chunk_mb" not in (persisted_upload or {}):
            UPLOAD_CONFIG.setdefault("ws_chunk_mb", 90)
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
