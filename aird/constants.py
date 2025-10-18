import os
from cloud import CloudManager

# Will be set in main() after parsing configuration
ACCESS_TOKEN = None
ADMIN_TOKEN = None
ROOT_DIR = os.getcwd()
DB_CONN = None
DB_PATH = None
CLOUD_MANAGER = CloudManager()
CLOUD_SHARE_FOLDER = ".aird_cloud"

FEATURE_FLAGS = {
    "file_upload": True,
    "file_delete": True,
    "file_rename": True,
    "file_download": True,
    "file_edit": True,
    "file_share": True,
    "compression": True,  # ✅ NEW: Enable gzip compression
    "super_search": True,  # ✅ NEW: Enable super search functionality
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


# Maximum upload size: reduced to 512 MB (Priority 2)
MAX_FILE_SIZE = 512 * 1024 * 1024
# Maximum size to load into editor: 5 MB (Priority 2)
MAX_READABLE_FILE_SIZE = 5 * 1024 * 1024
CHUNK_SIZE = 1024 * 64
# Minimum file size to use mmap (avoid overhead for small files)
MMAP_MIN_SIZE = 1024 * 1024  # 1MB

# Allowed upload extensions (whitelist) to prevent dangerous uploads (Priority 1)
ALLOWED_UPLOAD_EXTENSIONS = {
    # Text and data
    ".txt", ".log", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
    # Archives (read-only; still safe to store)
    ".zip", ".tar", ".gz", ".bz2", ".xz",
    # Code snippets (store-only, not executed)
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".sh"
}
# Allow override via env for controlled environments
_env_exts = os.environ.get("AIRD_ALLOWED_UPLOAD_EXTENSIONS")
if _env_exts:
    try:
        ALLOWED_UPLOAD_EXTENSIONS = {"." + e.strip().lstrip(".").lower() for e in _env_exts.split(",") if e.strip()}
    except Exception:
        pass

# SHARES = {}  # REMOVED: Using database-only persistence