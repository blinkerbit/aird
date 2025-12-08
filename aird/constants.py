import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from aird.cloud import CloudManager

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
    "compression": True,  # ✅ NEW: Enable gzip compression
    "super_search": True,  # ✅ NEW: Enable super search functionality
    "p2p_transfer": True,  # ✅ NEW: Enable P2P file transfer
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

# File operation constants
MAX_FILE_SIZE = 512 * 1024 * 1024  # 512 MB
MAX_READABLE_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_UPLOAD_EXTENSIONS = {
    ".txt", ".log", ".md", ".json", ".xml", ".yaml", ".yml", ".csv",
    ".jpg", ".jpeg", ".png", ".gif", ".ico",
    ".mp4", ".webm", ".ogg", ".mp3", ".wav",
    ".pdf", ".zip", ".gz", ".tar", ".bz2",
}

# Mmap constants
MMAP_MIN_SIZE = 1 * 1024 * 1024  # 1 MB
CHUNK_SIZE = 64 * 1024  # 64 KB

# SHARES = {}  # REMOVED: Using database-only persistence

# Rate limiting
LOGIN_RATE_LIMIT_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW = 300  # 5 minutes
