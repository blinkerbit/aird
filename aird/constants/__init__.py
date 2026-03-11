"""Constants package - runtime config, feature flags, and message strings."""

import os
import sys

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

# Hard ceiling for Tornado server — admin cannot exceed this
MAX_UPLOAD_FILE_SIZE_HARD_LIMIT = 10 * 1024 * 1024 * 1024  # 10 GB

# File operation constants (derived from UPLOAD_CONFIG at startup)
MAX_FILE_SIZE = UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
MAX_READABLE_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
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

# Network share manager (set at startup)
NETWORK_SHARE_MANAGER = None

# Rate limiting
LOGIN_RATE_LIMIT_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW = 300  # 5 minutes
