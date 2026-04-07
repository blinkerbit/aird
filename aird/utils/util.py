import logging
import os
import json
import sqlite3
import traceback
from datetime import datetime
import weakref
import threading
import time
import tornado.ioloop
import aird.constants as constants_module
from aird.constants import (
    FEATURE_FLAGS,
    WEBSOCKET_CONFIG,
)
from aird.constants.media import (
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    SPECIAL_FILENAMES,
    EXTENSION_ICONS,
)
from aird.db import load_feature_flags, DB_CONN, load_websocket_config
from aird.sql_identifiers import format_select_columns, format_shares_select_sql
from aird.core.filter_expression import FilterExpression  # noqa: F401
from aird.core.file_operations import (  # noqa: F401
    get_all_files_recursive,
    matches_glob_patterns,
    filter_files_by_patterns,
    cloud_root_dir,
    ensure_share_cloud_dir,
    sanitize_cloud_filename,
    is_cloud_relative_path,
    remove_cloud_file_if_exists,
    cleanup_share_cloud_dir_if_empty,
    remove_share_cloud_dir,
    download_cloud_item,
    download_cloud_items,
)


def _parse_json_field(json_str, default):
    """Parse a JSON string, returning default on any error or if falsy."""
    if not json_str:
        return default
    try:
        return json.loads(json_str)
    except (ValueError, TypeError):
        return default


_SHARE_LOAD_OPTIONAL_COLS = [
    "allowed_users",
    "secret_token",
    "share_type",
    "allow_list",
    "avoid_list",
    "expiry_date",
]
_SHARE_LOAD_COLS_ALLOWED = frozenset(
    ["id", "created", "paths", *_SHARE_LOAD_OPTIONAL_COLS]
)


def _load_share_col_names(conn: sqlite3.Connection) -> list:
    """Return the column list to SELECT from shares, based on available schema."""
    cursor = conn.execute("PRAGMA table_info(shares)")
    available = {row[1] for row in cursor.fetchall()}
    return ["id", "created", "paths"] + [
        c for c in _SHARE_LOAD_OPTIONAL_COLS if c in available
    ]


def _share_row_to_dict(row: tuple, col_names: list) -> dict:
    """Convert a raw shares DB row to the canonical share dict."""
    d = dict(zip(col_names, row))
    return {
        "paths": _parse_json_field(d.get("paths"), []),
        "created": d["created"],
        "allowed_users": _parse_json_field(d.get("allowed_users"), None),
        "secret_token": d.get("secret_token"),
        "share_type": d.get("share_type") or "static",
        "allow_list": _parse_json_field(d.get("allow_list"), []),
        "avoid_list": _parse_json_field(d.get("avoid_list"), []),
        "expiry_date": d.get("expiry_date"),
    }


def _load_shares(conn: sqlite3.Connection) -> dict:
    loaded: dict = {}
    try:
        col_names = _load_share_col_names(conn)
        cols = format_select_columns(col_names, _SHARE_LOAD_COLS_ALLOWED)
        rows = conn.execute(format_shares_select_sql(cols)).fetchall()
        for row in rows:
            d = dict(zip(col_names, row))
            loaded[d["id"]] = _share_row_to_dict(row, col_names)
    except Exception as e:
        print(f"Error loading shares: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return {}
    return loaded


def get_file_icon(filename: str) -> str:
    """Return an emoji icon for the given filename.

    Resolution order:
    1. Exact filename match (case-insensitive) from SPECIAL_FILENAMES.
    2. Files whose name starts with ``.env`` get a lock icon.
    3. Video / audio extensions (using the canonical sets).
    4. Extension lookup via EXTENSION_ICONS.
    5. Default fallback.
    """
    lower = filename.lower()
    # 1. Special filenames
    if lower in SPECIAL_FILENAMES:
        return SPECIAL_FILENAMES[lower]
    # 2. .env* files
    if filename.startswith(".env"):
        return "🔐"

    ext = os.path.splitext(filename)[1].lower()
    # 3. Media types via canonical sets
    if ext in VIDEO_EXTENSIONS:
        return "🎬"
    if ext in AUDIO_EXTENSIONS:
        return "🎵"
    # 4. Extension table lookup
    return EXTENSION_ICONS.get(ext, "📦")


def format_size(size: int) -> str:
    """Format size in bytes to human readable string"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


class WebSocketConnectionManager:
    def __init__(
        self, config_prefix="ws", default_max_connections=100, default_idle_timeout=60
    ):
        self.connections: set = set()
        self.config_prefix = config_prefix
        self.default_max_connections = default_max_connections
        self.default_idle_timeout = default_idle_timeout
        self.connection_times = weakref.WeakKeyDictionary()
        self.last_activity = weakref.WeakKeyDictionary()
        self._cleanup_lock = threading.RLock()

        # Start periodic cleanup
        self._setup_cleanup_timer()

    @property
    def max_connections(self) -> int:
        """Get current max connections from configuration"""
        config = get_current_websocket_config()
        return config.get(
            f"{self.config_prefix}_max_connections", self.default_max_connections
        )

    @property
    def idle_timeout(self) -> int:
        """Get current idle timeout from configuration"""
        config = get_current_websocket_config()
        return config.get(
            f"{self.config_prefix}_idle_timeout", self.default_idle_timeout
        )

    def _setup_cleanup_timer(self):
        """Setup periodic cleanup of dead and idle connections"""

        def cleanup():
            self.cleanup_dead_connections()
            self.cleanup_idle_connections()
            # Schedule next cleanup
            tornado.ioloop.IOLoop.current().call_later(60, cleanup)

        # Start cleanup in 60 seconds
        tornado.ioloop.IOLoop.current().call_later(60, cleanup)

    def add_connection(self, connection) -> bool:
        """Add a connection if under limit. Returns True if added."""
        with self._cleanup_lock:
            if len(self.connections) >= self.max_connections:
                return False

            self.connections.add(connection)
            self.connection_times[connection] = time.time()
            self.last_activity[connection] = time.time()
            return True

    def remove_connection(self, connection):
        """Remove a connection safely"""
        with self._cleanup_lock:
            self.connections.discard(connection)
            self.connection_times.pop(connection, None)
            self.last_activity.pop(connection, None)

    def update_activity(self, connection):
        """Update last activity time for a connection"""
        self.last_activity[connection] = time.time()

    def cleanup_dead_connections(self):
        """Remove connections that can't receive messages"""
        with self._cleanup_lock:
            dead_connections = set()
            for conn in self.connections:
                try:
                    # Try to ping the connection
                    if hasattr(conn, "ws_connection") and conn.ws_connection:
                        conn.ping()
                    else:
                        # Connection is closed
                        dead_connections.add(conn)
                except Exception:
                    dead_connections.add(conn)

            for conn in dead_connections:
                self.remove_connection(conn)

    def cleanup_idle_connections(self):
        """Remove connections that have been idle too long"""
        with self._cleanup_lock:
            current_time = time.time()
            idle_connections = set()

            for conn in self.connections:
                last_activity = self.last_activity.get(conn, 0)
                if current_time - last_activity > self.idle_timeout:
                    idle_connections.add(conn)

            for conn in idle_connections:
                try:
                    if hasattr(conn, "close"):
                        conn.close(code=1000, reason="Idle timeout")
                except Exception:
                    logging.getLogger(__name__).debug(
                        "WebSocket close failed during idle cleanup", exc_info=True
                    )
                self.remove_connection(conn)

    def get_stats(self) -> dict:
        """Get connection statistics"""
        with self._cleanup_lock:
            current_time = time.time()
            return {
                "active_connections": len(self.connections),
                "max_connections": self.max_connections,
                "idle_timeout": self.idle_timeout,
                "oldest_connection_age": max(
                    (
                        current_time - self.connection_times.get(conn, current_time)
                        for conn in self.connections
                    ),
                    default=0,
                ),
                "average_connection_age": (
                    sum(
                        current_time - self.connection_times.get(conn, current_time)
                        for conn in self.connections
                    )
                    / len(self.connections)
                    if self.connections
                    else 0
                ),
            }

    def broadcast_message(self, message, filter_func=None):
        """Broadcast message to all connections with optional filtering"""
        with self._cleanup_lock:
            dead_connections = set()
            for conn in self.connections:
                try:
                    if filter_func is None or filter_func(conn):
                        if hasattr(conn, "write_message"):
                            conn.write_message(message)
                        self.update_activity(conn)
                except Exception:
                    dead_connections.add(conn)

            # Remove dead connections
            for conn in dead_connections:
                self.remove_connection(conn)


def get_files_in_directory(path="."):
    files = []
    for entry in os.scandir(path):
        stat = entry.stat()
        files.append(
            {
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size_bytes": stat.st_size,
                "size_str": (
                    f"{stat.st_size / 1024:.2f} KB" if not entry.is_dir() else "-"
                ),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "modified_timestamp": int(stat.st_mtime),
            }
        )
    return files


def is_video_file(filename):
    """Check if file is a supported video format"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in VIDEO_EXTENSIONS


def is_audio_file(filename):
    """Check if file is a supported audio format"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in AUDIO_EXTENSIONS


_feature_flags_cache: dict | None = None
_feature_flags_cache_ts: float = 0.0
_FEATURE_FLAGS_TTL = 5.0  # seconds


def invalidate_feature_flags_cache() -> None:
    """Call after updating feature flags to force a fresh read on next access."""
    global _feature_flags_cache, _feature_flags_cache_ts
    _feature_flags_cache = None
    _feature_flags_cache_ts = 0.0


def _merge_flags(current: dict, persisted: dict) -> dict:
    """Merge persisted DB flags with in-memory flags; in-memory takes precedence."""
    merged = persisted.copy()
    for k, v in current.items():
        merged[k] = bool(v)
    # Also include any DB-only flags not yet in merged
    for k, v in persisted.items():
        if k not in merged:
            merged[k] = bool(v)
    return merged


def get_current_feature_flags() -> dict:
    """Return current feature flags with in-memory changes taking precedence over DB.
    Results are cached for a short TTL to avoid hitting the database on every request.
    Falls back to in-memory defaults if DB is unavailable.
    """
    global _feature_flags_cache, _feature_flags_cache_ts

    now = time.time()
    if (
        _feature_flags_cache is not None
        and (now - _feature_flags_cache_ts) < _FEATURE_FLAGS_TTL
    ):
        return _feature_flags_cache.copy()

    # Start with in-memory flags (which may have just been updated)
    current = FEATURE_FLAGS.copy()

    db_conn = constants_module.DB_CONN
    if db_conn is not None:
        try:
            persisted = load_feature_flags(db_conn)
            if persisted:
                merged = _merge_flags(current, persisted)
                _feature_flags_cache = merged
                _feature_flags_cache_ts = now
                return merged.copy()
        except Exception:
            logging.getLogger(__name__).debug(
                "load_feature_flags failed; using in-memory flags", exc_info=True
            )

    _feature_flags_cache = current
    _feature_flags_cache_ts = now
    return current


def get_current_websocket_config() -> dict:
    """Return current WebSocket configuration with SQLite values taking precedence.
    Falls back to in-memory defaults if DB is unavailable.
    """
    current = WEBSOCKET_CONFIG.copy()
    if DB_CONN is not None:
        try:
            persisted = load_websocket_config(DB_CONN)
            if persisted:
                # Persisted values override runtime defaults
                for k, v in persisted.items():
                    current[k] = int(v)
        except Exception:
            logging.getLogger(__name__).debug(
                "load_websocket_config failed; using defaults", exc_info=True
            )
    return current


def is_feature_enabled(key: str, default: bool = False) -> bool:
    flags = get_current_feature_flags()
    return bool(flags.get(key, default))


def augment_with_shared_status(
    files: list[dict], current_path: str, all_shares: dict
) -> None:
    """Updates file metadata dicts in-place with an 'is_shared' boolean."""
    all_shared_paths = set()
    for share in all_shares.values():
        for p in share.get("paths", []):
            all_shared_paths.add(p)

    for file_info in files:
        full_path = os.path.join(current_path, file_info["name"]).replace("\\", "/")
        file_info["is_shared"] = full_path in all_shared_paths
