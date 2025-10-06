import os
import secrets
import argparse
import json
from typing import Set
import logging
import asyncio
import mmap
import sys
import sqlite3
import fnmatch
import glob
import pathlib
import ssl

import tornado.ioloop
import tornado.web
import socket
import tornado.websocket
import tornado.escape as tornado_escape
import shutil
from collections import deque
from ldap3 import Server, Connection, ALL
from datetime import datetime
import gzip
import threading
import time
import mimetypes
from io import BytesIO
import tempfile
from urllib.parse import unquote, urlparse
import aiofiles
import asyncio
import concurrent.futures
import re
import shlex
import time
import threading
import weakref
import hashlib
# Secure password hashing (Priority 1)
try:
    from argon2 import PasswordHasher
    from argon2 import exceptions as argon2_exceptions
    ARGON2_AVAILABLE = True
    PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
except Exception:
    ARGON2_AVAILABLE = False
    PH = None

# Import Rust integration with fallback
try:
    from .rust_integration import (
        HybridFileHandler,
        HybridCompressionHandler,
        RUST_AVAILABLE
    )
    # Log Rust availability
    logger = logging.getLogger(__name__)
    if RUST_AVAILABLE:
        logger.info("🚀 Rust core extensions loaded - performance mode enabled!")
    else:
        logger.info("⚠️  Rust extensions not available, using Python fallbacks")
except ImportError:
    # Fallback if rust_integration module doesn't exist yet
    RUST_AVAILABLE = False
    HybridFileHandler = None
    HybridCompressionHandler = None

def join_path(*parts):
    return os.path.join(*parts).replace("\\", "/")

# Path security helper (Priority 1)
def is_within_root(path: str, root: str) -> bool:
    """Return True if path is within root after resolving symlinks and normalization."""
    try:
        path_real = os.path.realpath(path)
        root_real = os.path.realpath(root)
        return os.path.commonpath([path_real, root_real]) == root_real
    except Exception:
        return False

# WebSocket origin validation helper (Priority 2)
def is_valid_websocket_origin(handler, origin: str) -> bool:
    try:
        if not origin:
            return False
        parsed = urlparse(origin)
        origin_host = parsed.hostname
        origin_port = parsed.port
        origin_scheme = parsed.scheme
        # Determine expected host/port from request
        req_host = handler.request.host.split(":")[0]
        try:
            req_port = int(handler.request.host.split(":")[1])
        except (IndexError, ValueError):
            req_port = 443 if handler.request.protocol == "https" else 80
        expected_scheme = "https" if handler.request.protocol == "https" else "http"
        if origin_scheme not in (expected_scheme, expected_scheme + "s"):
            # Allow ws/wss equivalents to http/https
            if not (origin_scheme in ("ws", "wss") and expected_scheme in ("http", "https")):
                return False
        if origin_host != req_host:
            # Allow localhost in development if explicitly enabled
            allow_dev = bool(handler.settings.get("allow_dev_origins", False))
            if not (allow_dev and origin_host in {"localhost", "127.0.0.1"}):
                return False
        if origin_port and origin_port != req_port:
            # Different port -> reject unless allow_dev and localhost
            allow_dev = bool(handler.settings.get("allow_dev_origins", False))
            if not (allow_dev and origin_host in {"localhost", "127.0.0.1"}):
                return False
        return True
    except Exception:
        return False

# Add this import for template path
from tornado.web import RequestHandler, Application

# Will be set in main() after parsing configuration
ACCESS_TOKEN = None
ADMIN_TOKEN = None
ROOT_DIR = os.getcwd()
DB_CONN = None
DB_PATH = None

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

class WebSocketConnectionManager:
    """Base class for managing WebSocket connections with memory leak prevention"""
    
    def __init__(self, config_prefix: str, default_max_connections: int = 100, default_idle_timeout: int = 300):
        self.connections: Set = set()
        self.config_prefix = config_prefix
        self.default_max_connections = default_max_connections
        self.default_idle_timeout = default_idle_timeout
        self.connection_times = weakref.WeakKeyDictionary()
        self.last_activity = weakref.WeakKeyDictionary()
        self._cleanup_lock = threading.Lock()
        
        # Start periodic cleanup
        self._setup_cleanup_timer()
    
    @property
    def max_connections(self) -> int:
        """Get current max connections from configuration"""
        config = get_current_websocket_config()
        return config.get(f"{self.config_prefix}_max_connections", self.default_max_connections)
    
    @property
    def idle_timeout(self) -> int:
        """Get current idle timeout from configuration"""
        config = get_current_websocket_config()
        return config.get(f"{self.config_prefix}_idle_timeout", self.default_idle_timeout)
    
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
            for conn in list(self.connections):
                try:
                    # Try to ping the connection
                    if hasattr(conn, 'ws_connection') and conn.ws_connection:
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
            
            for conn in list(self.connections):
                last_activity = self.last_activity.get(conn, 0)
                if current_time - last_activity > self.idle_timeout:
                    idle_connections.add(conn)
            
            for conn in idle_connections:
                try:
                    if hasattr(conn, 'close'):
                        conn.close(code=1000, reason="Idle timeout")
                except Exception:
                    pass
                self.remove_connection(conn)
    
    def get_stats(self) -> dict:
        """Get connection statistics"""
        with self._cleanup_lock:
            current_time = time.time()
            return {
                'active_connections': len(self.connections),
                'max_connections': self.max_connections,
                'idle_timeout': self.idle_timeout,
                'oldest_connection_age': max(
                    (current_time - self.connection_times.get(conn, current_time) 
                     for conn in self.connections), 
                    default=0
                ),
                'average_connection_age': sum(
                    current_time - self.connection_times.get(conn, current_time) 
                    for conn in self.connections
                ) / len(self.connections) if self.connections else 0
            }
    
    def broadcast_message(self, message, filter_func=None):
        """Broadcast message to all connections with optional filtering"""
        with self._cleanup_lock:
            dead_connections = set()
            for conn in list(self.connections):
                try:
                    if filter_func is None or filter_func(conn):
                        if hasattr(conn, 'write_message'):
                            conn.write_message(message)
                        self.update_activity(conn)
                except Exception:
                    dead_connections.add(conn)
            
            # Remove dead connections
            for conn in dead_connections:
                self.remove_connection(conn)

class FilterExpression:
    """Parse and evaluate complex filter expressions with AND/OR logic"""
    
    def __init__(self, expression: str):
        self.original_expression = expression
        self.parsed_expression = self._parse(expression)
    
    def _parse(self, expression: str):
        """Parse filter expression into evaluable structure"""
        if not expression or not expression.strip():
            return None
            
        expression = expression.strip()
        
        # Handle escaped expressions (prefix with backslash to force literal interpretation)
        if expression.startswith('\\'):
            return {'type': 'term', 'value': expression[1:].strip('"')}
        
        # Handle quoted expressions (always literal)
        if ((expression.startswith('"') and expression.endswith('"')) or 
            (expression.startswith("'") and expression.endswith("'"))):
            return {'type': 'term', 'value': expression[1:-1]}
        
        # Check if this looks like a logical expression
        # Use word boundary regex to detect standalone AND/OR operators
        has_logical_operators = (
            re.search(r'\bAND\b', expression, re.IGNORECASE) or
            re.search(r'\bOR\b', expression, re.IGNORECASE)
        )
        
        # Additional check: make sure these are actually surrounded by whitespace (logical operators)
        if has_logical_operators:
            # Verify these are standalone words, not part of other words
            and_matches = list(re.finditer(r'\bAND\b', expression, re.IGNORECASE))
            or_matches = list(re.finditer(r'\bOR\b', expression, re.IGNORECASE))
            
            has_logical_and = any(
                self._is_standalone_operator_static(expression, match.start(), match.end())
                for match in and_matches
            )
            has_logical_or = any(
                self._is_standalone_operator_static(expression, match.start(), match.end())
                for match in or_matches
            )
            
            has_logical_operators = has_logical_and or has_logical_or
        
        if not has_logical_operators:
            return {'type': 'term', 'value': expression.strip('"')}
        
        # Parse complex expressions
        return self._parse_complex(expression)
    
    def _parse_complex(self, expression: str):
        """Parse complex expressions with AND/OR and parentheses"""
        try:
            # Handle parentheses first by balancing them
            expression = expression.strip()
            
            # If the entire expression is wrapped in parentheses, remove them
            if expression.startswith('(') and expression.endswith(')'):
                # Check if parentheses are balanced
                if self._is_balanced_parentheses(expression):
                    return self._parse_complex(expression[1:-1])
            
            # Find OR outside of parentheses (lower precedence)
            or_parts = self._split_respecting_parentheses(expression, 'OR')
            if len(or_parts) > 1:
                return {
                    'type': 'or',
                    'operands': [self._parse_and_part(part.strip()) for part in or_parts]
                }
            
            # If no OR, try AND
            return self._parse_and_part(expression)
            
        except Exception:
            # Fallback to simple term matching on parse error
            return {'type': 'term', 'value': expression.strip('"')}
    
    def _parse_and_part(self, expression: str):
        """Parse AND expressions"""
        and_parts = self._split_respecting_parentheses(expression, 'AND')
        if len(and_parts) > 1:
            return {
                'type': 'and',
                'operands': [self._parse_term(part.strip()) for part in and_parts]
            }
        return self._parse_term(expression.strip())
    
    def _parse_term(self, term: str):
        """Parse individual terms, handling quotes and parentheses"""
        term = term.strip()
        
        # Handle parentheses
        if term.startswith('(') and term.endswith(')'):
            return self._parse_complex(term[1:-1])
        
        # Handle quoted terms
        if (term.startswith('"') and term.endswith('"')) or (term.startswith("'") and term.endswith("'")):
            return {'type': 'term', 'value': term[1:-1]}
        
        return {'type': 'term', 'value': term}
    
    def matches(self, line: str) -> bool:
        """Evaluate if a line matches the filter expression"""
        if self.parsed_expression is None:
            return True
        return self._evaluate(self.parsed_expression, line)
    
    def _evaluate(self, node, line: str) -> bool:
        """Recursively evaluate parsed expression against line"""
        if node['type'] == 'term':
            return node['value'].lower() in line.lower()
        elif node['type'] == 'and':
            return all(self._evaluate(operand, line) for operand in node['operands'])
        elif node['type'] == 'or':
            return any(self._evaluate(operand, line) for operand in node['operands'])
        return False
    
    def _split_respecting_parentheses(self, expression: str, operator: str):
        """Split expression by operator while respecting parentheses and word boundaries"""
        parts = []
        current_part = ""
        paren_depth = 0
        in_quotes = False
        quote_char = None
        i = 0
        
        while i < len(expression):
            char = expression[i]
            
            # Handle quotes
            if char in ['"', "'"] and not in_quotes:
                in_quotes = True
                quote_char = char
            elif char == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
            
            # Skip everything inside quotes
            if in_quotes:
                current_part += char
                i += 1
                continue
            
            # Handle parentheses
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth -= 1
            
            # Check for operator when we're at the top level
            if paren_depth == 0:
                # Check if we're at the start of the operator
                remaining = expression[i:]
                # Pattern: operator with word boundaries, possibly with whitespace
                op_pattern = f'\\b{re.escape(operator)}\\b'
                match = re.match(op_pattern, remaining, re.IGNORECASE)
                if match:
                    # Verify this is actually a logical operator by checking context
                    operator_start = i
                    operator_end = i + len(match.group(0))
                    
                    # Check if surrounded by whitespace or start/end of string
                    before_ok = operator_start == 0 or expression[operator_start - 1].isspace()
                    after_ok = operator_end >= len(expression) or expression[operator_end].isspace()
                    
                    if before_ok and after_ok:
                        # Found operator at top level
                        parts.append(current_part.strip())
                        current_part = ""
                        # Skip the operator and any following whitespace
                        i = operator_end
                        while i < len(expression) and expression[i].isspace():
                            i += 1
                        continue
            
            current_part += char
            i += 1
        
        if current_part.strip():
            parts.append(current_part.strip())
        
        return parts if len(parts) > 1 else [expression]
    
    
    def _is_balanced_parentheses(self, expression: str):
        """Check if parentheses are balanced"""
        depth = 0
        in_quotes = False
        quote_char = None
        
        for char in expression:
            if char in ['"', "'"] and not in_quotes:
                in_quotes = True
                quote_char = char
            elif char == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
            elif not in_quotes:
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth < 0:
                        return False
        
        return depth == 0
    
    def _is_standalone_operator(self, expression: str, start: int, end: int, operator: str):
        """Check if AND/OR at this position is a standalone logical operator"""
        return self._is_standalone_operator_static(expression, start, end)
    
    @staticmethod
    def _is_standalone_operator_static(expression: str, start: int, end: int):
        """Static version of _is_standalone_operator for use during parsing"""
        # Check if surrounded by whitespace (indicating it's a logical operator)
        before_space = start == 0 or expression[start - 1].isspace()
        after_space = end >= len(expression) or expression[end].isspace()
        
        return before_space and after_space

    def __str__(self):
        return f"FilterExpression({self.original_expression})"

# ------------------------
# SQLite persistence layer
# ------------------------

def _get_data_dir() -> str:
    """Return OS-appropriate data directory for storing the SQLite DB."""
    try:
        if os.name == 'nt':  # Windows
            base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or os.path.expanduser('~\\AppData\\Local')
        elif sys.platform == 'darwin':  # macOS
            base = os.path.expanduser('~/Library/Application Support')
        else:  # Linux and others
            base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
        data_dir = os.path.join(base, 'aird')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    except Exception:
        # Fallback to current directory
        return os.getcwd()

def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_flags (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shares (
            id TEXT PRIMARY KEY,
            created TEXT NOT NULL,
            paths TEXT NOT NULL,
            allowed_users TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_login TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ldap_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            server TEXT NOT NULL,
            ldap_base_dn TEXT NOT NULL,
            ldap_member_attributes TEXT NOT NULL DEFAULT 'member',
            user_template TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ldap_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER NOT NULL,
            sync_type TEXT NOT NULL,
            users_found INTEGER NOT NULL,
            users_created INTEGER NOT NULL,
            users_removed INTEGER NOT NULL,
            sync_time TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT,
            FOREIGN KEY (config_id) REFERENCES ldap_configs (id)
        )
        """
    )

    # Migration for shares table
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(shares)")
    columns = [column[1] for column in cursor.fetchall()]
    if "allowed_users" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN allowed_users TEXT")
    if "secret_token" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN secret_token TEXT")
    if "share_type" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN share_type TEXT DEFAULT 'static'")
    if "allow_list" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN allow_list TEXT")
    if "avoid_list" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN avoid_list TEXT")
    if "expiry_date" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN expiry_date TEXT")

    conn.commit()

def _load_feature_flags(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute("SELECT key, value FROM feature_flags").fetchall()
        return {k: bool(v) for (k, v) in rows}
    except Exception:
        return {}

def _save_feature_flags(conn: sqlite3.Connection, flags: dict) -> None:
    try:
        with conn:
            for k, v in flags.items():
                conn.execute(
                    "REPLACE INTO feature_flags (key, value) VALUES (?, ?)",
                    (k, 1 if v else 0),
                )
    except Exception:
        pass

def _load_shares(conn: sqlite3.Connection) -> dict:
    loaded: dict = {}
    try:
        # Check if allowed_users and secret_token columns exist
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'allowed_users' in columns and 'secret_token' in columns and 'share_type' in columns and 'allow_list' in columns and 'avoid_list' in columns and 'expiry_date' in columns:
            rows = conn.execute("SELECT id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date FROM shares").fetchall()
            for sid, created, paths_json, allowed_users_json, secret_token, share_type, allow_list_json, avoid_list_json, expiry_date in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                try:
                    allowed_users = json.loads(allowed_users_json) if allowed_users_json else None
                except Exception:
                    allowed_users = None
                try:
                    allow_list = json.loads(allow_list_json) if allow_list_json else []
                except Exception:
                    allow_list = []
                try:
                    avoid_list = json.loads(avoid_list_json) if avoid_list_json else []
                except Exception:
                    avoid_list = []
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": allowed_users, "secret_token": secret_token, "share_type": share_type or "static", "allow_list": allow_list, "avoid_list": avoid_list, "expiry_date": expiry_date}
        elif 'allowed_users' in columns and 'secret_token' in columns and 'share_type' in columns:
            rows = conn.execute("SELECT id, created, paths, allowed_users, secret_token, share_type FROM shares").fetchall()
            for sid, created, paths_json, allowed_users_json, secret_token, share_type in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                try:
                    allowed_users = json.loads(allowed_users_json) if allowed_users_json else None
                except Exception:
                    allowed_users = None
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": allowed_users, "secret_token": secret_token, "share_type": share_type or "static", "allow_list": [], "avoid_list": [], "expiry_date": None}
        elif 'allowed_users' in columns and 'secret_token' in columns:
            rows = conn.execute("SELECT id, created, paths, allowed_users, secret_token FROM shares").fetchall()
            for sid, created, paths_json, allowed_users_json, secret_token in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                try:
                    allowed_users = json.loads(allowed_users_json) if allowed_users_json else None
                except Exception:
                    allowed_users = None
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": allowed_users, "secret_token": secret_token, "share_type": "static", "allow_list": [], "avoid_list": [], "expiry_date": None}
        elif 'allowed_users' in columns:
            rows = conn.execute("SELECT id, created, paths, allowed_users FROM shares").fetchall()
            for sid, created, paths_json, allowed_users_json in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                try:
                    allowed_users = json.loads(allowed_users_json) if allowed_users_json else None
                except Exception:
                    allowed_users = None
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": allowed_users, "secret_token": None, "share_type": "static", "allow_list": [], "avoid_list": [], "expiry_date": None}
        else:
            # Fallback for old schema without allowed_users column
            rows = conn.execute("SELECT id, created, paths FROM shares").fetchall()
            for sid, created, paths_json in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": None, "secret_token": None, "share_type": "static", "allow_list": [], "avoid_list": [], "expiry_date": None}
    except Exception as e:
        print(f"Error loading shares: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return {}
    return loaded

def _insert_share(conn: sqlite3.Connection, sid: str, created: str, paths: list[str], allowed_users: list[str] = None, secret_token: str = None, share_type: str = "static", allow_list: list[str] = None, avoid_list: list[str] = None, expiry_date: str = None) -> bool:
    try:
        with conn:
            conn.execute(
                "REPLACE INTO shares (id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, created, json.dumps(paths), json.dumps(allowed_users) if allowed_users else None, secret_token, share_type, json.dumps(allow_list) if allow_list else None, json.dumps(avoid_list) if avoid_list else None, expiry_date),
            )
        return True
    except Exception as e:
        logging.error(f"Failed to insert share {sid} into database: {e}")
        import traceback
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False

def _delete_share(conn: sqlite3.Connection, sid: str) -> None:
    try:
        with conn:
            conn.execute("DELETE FROM shares WHERE id = ?", (sid,))
    except Exception:
        pass

def _update_share(conn: sqlite3.Connection, sid: str, share_type: str = None, disable_token: bool = None, allow_list: list = None, avoid_list: list = None, secret_token: str = None, expiry_date: str = None, **kwargs) -> bool:
    """Update share information"""
    try:
        updates = []
        values = []

        # Handle new parameters
        if share_type is not None:
            updates.append("share_type = ?")
            values.append(share_type)
        
        if disable_token == True:
            logging.debug(f"_update_share - disable_token={disable_token}")
            logging.debug("Disabling token (setting to None)")
            updates.append("secret_token = ?")
            values.append(None)
        else:
            new_token = secrets.token_urlsafe(32) if secret_token is None else secret_token
            updates.append("secret_token = ?")
            values.append(new_token)

        
        if allow_list is not None:
            updates.append("allow_list = ?")
            values.append(json.dumps(allow_list) if allow_list else None)
        
        if avoid_list is not None:
            updates.append("avoid_list = ?")
            values.append(json.dumps(avoid_list) if avoid_list else None)
        
        if expiry_date is not None:
            updates.append("expiry_date = ?")
            values.append(expiry_date)

        # Handle legacy parameters
        valid_fields = ['allowed_users', 'paths']
        for field, value in kwargs.items():
            if field in valid_fields:
                updates.append(f"{field} = ?")
                if field in ['allowed_users', 'paths'] and value is not None:
                    values.append(json.dumps(value))
                else:
                    values.append(value)

        if not updates:
            return False

        values.append(sid)
        query = f"UPDATE shares SET {', '.join(updates)} WHERE id = ?"

        with conn:
            cursor = conn.execute(query, values)
            logging.debug(f"Update executed, rows affected: {cursor.rowcount}")
            
        return True
    except Exception as e:
        logging.error(f"Failed to update share {sid}: {e}")
        import traceback
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False

def _get_all_files_recursive(root_path: str, base_path: str = "") -> list:
    """Recursively get all files in a directory"""
    all_files = []
    try:
        for item in os.listdir(root_path):
            item_path = os.path.join(root_path, item)
            relative_path = os.path.join(base_path, item) if base_path else item
            
            if os.path.isfile(item_path):
                # It's a file, add it to the list
                all_files.append(relative_path)
            elif os.path.isdir(item_path):
                # It's a directory, recursively scan it
                sub_files = _get_all_files_recursive(item_path, relative_path)
                all_files.extend(sub_files)
    except (OSError, PermissionError) as e:
        print(f"Error scanning directory {root_path}: {e}")
    
    return all_files

def _matches_glob_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the given glob patterns"""
    if not patterns:
        return False
    
    import fnmatch
    for pattern in patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False

def _filter_files_by_patterns(files: list[str], allow_list: list[str] = None, avoid_list: list[str] = None) -> list[str]:
    """Filter files based on allow and avoid glob patterns"""
    if not files:
        return files
    
    filtered_files = []
    
    for file_path in files:
        # Check avoid list first (takes priority)
        if avoid_list and _matches_glob_patterns(file_path, avoid_list):
            print(f"DEBUG: File {file_path} excluded by avoid list")
            continue
        
        # Check allow list
        if allow_list:
            if _matches_glob_patterns(file_path, allow_list):
                print(f"DEBUG: File {file_path} included by allow list")
                filtered_files.append(file_path)
            else:
                print(f"DEBUG: File {file_path} excluded by allow list")
        else:
            # No allow list means all files are allowed (unless in avoid list)
            filtered_files.append(file_path)
    
    return filtered_files

def _is_share_expired(expiry_date: str) -> bool:
    """Check if a share has expired based on expiry_date using system time"""
    if not expiry_date:
        return False
    
    try:
        from datetime import datetime
        
        # Parse the expiry date and convert to naive datetime (system time)
        expiry_datetime = datetime.fromisoformat(expiry_date.replace('Z', ''))
        
        # Get current system time (naive datetime)
        current_datetime = datetime.now()
        
        # Simple comparison using system time
        is_expired = current_datetime > expiry_datetime
        logging.debug(f"Checking expiry: current={current_datetime}, expiry={expiry_datetime}, expired={is_expired}")
        return is_expired
    except Exception as e:
        logging.error(f"Error checking expiry date {expiry_date}: {e}")
        return False

def _cleanup_expired_shares(conn: sqlite3.Connection) -> int:
    """Remove expired shares from the database. Returns the number of shares deleted."""
    try:
        from datetime import datetime
        cursor = conn.execute("SELECT id, expiry_date FROM shares WHERE expiry_date IS NOT NULL")
        rows = cursor.fetchall()
        
        deleted_count = 0
        for share_id, expiry_date in rows:
            if _is_share_expired(expiry_date):
                with conn:
                    conn.execute("DELETE FROM shares WHERE id = ?", (share_id,))
                    deleted_count += 1
                    logging.info(f"Deleted expired share: {share_id}")
        
        return deleted_count
    except Exception as e:
        logging.error(f"Error cleaning up expired shares: {e}")
        return 0

def _get_share_by_id(conn: sqlite3.Connection, sid: str) -> dict:
    """Get a single share by ID from database"""
    try:
        logging.debug(f"_get_share_by_id called with sid='{sid}'")
        logging.debug(f"conn is {'None' if conn is None else 'available'}")
        
        # Check if secret_token column exists
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        logging.debug(f"Table columns: {columns}")
        
        if 'secret_token' in columns and 'share_type' in columns and 'allow_list' in columns and 'avoid_list' in columns and 'expiry_date' in columns:
            logging.debug(f"Using query with all columns including expiry_date")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json, secret_token, share_type, allow_list_json, avoid_list_json, expiry_date = row
                result = {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": secret_token,
                    "share_type": share_type or "static",
                    "allow_list": json.loads(allow_list_json) if allow_list_json else [],
                    "avoid_list": json.loads(avoid_list_json) if avoid_list_json else [],
                    "expiry_date": expiry_date
                }
                logging.debug(f"Returning share data: {result}")
                return result
        elif 'secret_token' in columns and 'share_type' in columns:
            logging.debug(f"Using query with secret_token and share_type columns")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token, share_type FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json, secret_token, share_type = row
                result = {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": secret_token,
                    "share_type": share_type or "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None
                }
                logging.debug(f"Returning share data: {result}")
                return result
        elif 'secret_token' in columns:
            logging.debug(f"Using query with secret_token column")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json, secret_token = row
                result = {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": secret_token,
                    "share_type": "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None
                }
                logging.debug(f"Returning share data: {result}")
                return result
        else:
            logging.debug(f"Using query without secret_token column")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json = row
                result = {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": None,
                    "share_type": "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None
                }
                logging.debug(f"Returning share data: {result}")
                return result
        
        logging.debug(f"No share found for sid='{sid}'")
        return None
    except Exception as e:
        logging.error(f"Error getting share {sid}: {e}")
        import traceback
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return None

def _get_all_shares(conn: sqlite3.Connection) -> dict:
    """Get all shares from database"""
    try:
        # Check if secret_token column exists
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'secret_token' in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares"
            )
            shares = {}
            for row in cursor:
                sid, created, paths_json, allowed_users_json, secret_token = row
                shares[sid] = {
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": secret_token
                }
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares"
            )
            shares = {}
            for row in cursor:
                sid, created, paths_json, allowed_users_json = row
                shares[sid] = {
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": None
                }
        return shares
    except Exception as e:
        print(f"Error getting all shares: {e}")
        return {}

def _get_shares_for_path(conn: sqlite3.Connection, file_path: str) -> list:
    """Get all shares that contain a specific file path"""
    try:
        # Check if secret_token column exists
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'secret_token' in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares"
            )
            matching_shares = []
            for row in cursor:
                sid, created, paths_json, allowed_users_json, secret_token = row
                paths = json.loads(paths_json) if paths_json else []
                if file_path in paths:
                    matching_shares.append({
                        "id": sid,
                        "created": created,
                        "paths": paths,
                        "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                        "secret_token": secret_token
                    })
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares"
            )
            matching_shares = []
            for row in cursor:
                sid, created, paths_json, allowed_users_json = row
                paths = json.loads(paths_json) if paths_json else []
                if file_path in paths:
                    matching_shares.append({
                        "id": sid,
                        "created": created,
                        "paths": paths,
                        "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                        "secret_token": None
                    })
        return matching_shares
    except Exception as e:
        print(f"Error getting shares for path {file_path}: {e}")
        return []

def _load_websocket_config(conn: sqlite3.Connection) -> dict:
    """Load WebSocket configuration from SQLite database."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)")
        rows = conn.execute("SELECT key, value FROM websocket_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}

def _save_websocket_config(conn: sqlite3.Connection, config: dict) -> None:
    """Save WebSocket configuration to SQLite database."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)")
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO websocket_config (key, value) VALUES (?, ?)",
                    (key, int(value)),
                )
    except Exception:
        pass

# ------------------------
# User management functions
# ------------------------

def _hash_password(password: str) -> str:
    """Hash a password using Argon2 (Priority 1). Falls back to legacy only if Argon2 unavailable."""
    if ARGON2_AVAILABLE and PH is not None:
        return PH.hash(password)
    # Legacy fallback (not recommended): salted SHA-256
    salt = secrets.token_hex(32)
    pwd_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return f"{salt}:{pwd_hash}"

def _verify_password(password: str, password_hash: str) -> bool:
    """Verify password supporting Argon2 and legacy salted SHA-256."""
    # Try Argon2 first
    if password_hash and password_hash.startswith("$argon2") and ARGON2_AVAILABLE and PH is not None:
        try:
            return PH.verify(password_hash, password)
        except argon2_exceptions.VerifyMismatchError:
            return False
        except Exception:
            return False
    # Legacy format: salt:hash
    try:
        salt, stored_hash = password_hash.split(':', 1)
        pwd_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
        return pwd_hash == stored_hash
    except Exception:
        return False

def _create_user(conn: sqlite3.Connection, username: str, password: str, role: str = 'user') -> dict:
    """Create a new user in the database"""
    try:
        password_hash = _hash_password(password)
        created_at = datetime.now().isoformat()
        
        with conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, created_at)
            )
            user_id = cursor.lastrowid
            
        return {
            "id": user_id,
            "username": username,
            "role": role,
            "created_at": created_at,
            "active": True,
            "last_login": None
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{username}' already exists")
    except Exception as e:
        raise Exception(f"Failed to create user: {str(e)}")

def _get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    """Get user by username"""
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at, active, last_login FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        
        if row:
            return {
                "id": row[0],
                "username": row[1],
                "password_hash": row[2],
                "role": row[3],
                "created_at": row[4],
                "active": bool(row[5]),
                "last_login": row[6]
            }
        return None
    except Exception:
        return None

def _get_all_users(conn: sqlite3.Connection) -> list[dict]:
    """Get all users from the database"""
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, active, last_login FROM users ORDER BY created_at DESC"
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5]
            }
            for row in rows
        ]
    except Exception:
        return []

def _search_users(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Search users by username (case-insensitive)"""
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, active, last_login FROM users WHERE username LIKE ? AND active = 1 ORDER BY username LIMIT 20",
            (f"%{query}%",)
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5]
            }
            for row in rows
        ]
    except Exception:
        return []

def _update_user(conn: sqlite3.Connection, user_id: int, **kwargs) -> bool:
    """Update user information"""
    try:
        valid_fields = ['username', 'password_hash', 'role', 'active', 'last_login']
        updates = []
        values = []
        
        for field, value in kwargs.items():
            if field in valid_fields:
                if field == 'password' and value:  # Special handling for password
                    updates.append('password_hash = ?')
                    values.append(_hash_password(value))
                elif field == 'active':
                    updates.append('active = ?')
                    values.append(1 if value else 0)
                else:
                    updates.append(f'{field} = ?')
                    values.append(value)
        
        if not updates:
            return False
            
        values.append(user_id)
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
        
        with conn:
            conn.execute(query, values)
        return True
    except Exception:
        return False

def _create_ldap_config(conn: sqlite3.Connection, name: str, server: str, ldap_base_dn: str, 
                       ldap_member_attributes: str, user_template: str) -> dict:
    """Create a new LDAP configuration"""
    try:
        created_at = datetime.now().isoformat()
        
        with conn:
            cursor = conn.execute(
                """INSERT INTO ldap_configs (name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at)
            )
            config_id = cursor.lastrowid
            
        return {
            "id": config_id,
            "name": name,
            "server": server,
            "ldap_base_dn": ldap_base_dn,
            "ldap_member_attributes": ldap_member_attributes,
            "user_template": user_template,
            "created_at": created_at,
            "active": True
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"LDAP configuration '{name}' already exists")
    except Exception as e:
        raise Exception(f"Failed to create LDAP configuration: {str(e)}")

def _get_all_ldap_configs(conn: sqlite3.Connection) -> list[dict]:
    """Get all LDAP configurations"""
    try:
        rows = conn.execute(
            "SELECT id, name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at, active FROM ldap_configs ORDER BY created_at DESC"
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "name": row[1],
                "server": row[2],
                "ldap_base_dn": row[3],
                "ldap_member_attributes": row[4],
                "user_template": row[5],
                "created_at": row[6],
                "active": bool(row[7])
            }
            for row in rows
        ]
    except Exception:
        return []

def _get_ldap_config_by_id(conn: sqlite3.Connection, config_id: int) -> dict | None:
    """Get LDAP configuration by ID"""
    try:
        row = conn.execute(
            "SELECT id, name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at, active FROM ldap_configs WHERE id = ?",
            (config_id,)
        ).fetchone()
        
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "server": row[2],
                "ldap_base_dn": row[3],
                "ldap_member_attributes": row[4],
                "user_template": row[5],
                "created_at": row[6],
                "active": bool(row[7])
            }
    except Exception:
        return None

def _update_ldap_config(conn: sqlite3.Connection, config_id: int, **kwargs) -> bool:
    """Update LDAP configuration"""
    try:
        valid_fields = ['name', 'server', 'ldap_base_dn', 'ldap_member_attributes', 'user_template', 'active']
        updates = []
        values = []
        
        for field, value in kwargs.items():
            if field in valid_fields:
                if field == 'active':
                    updates.append('active = ?')
                    values.append(1 if value else 0)
                else:
                    updates.append(f'{field} = ?')
                    values.append(value)
        
        if not updates:
            return False
            
        values.append(config_id)
        query = f"UPDATE ldap_configs SET {', '.join(updates)} WHERE id = ?"
        
        with conn:
            conn.execute(query, values)
        return True
    except Exception:
        return False

def _delete_ldap_config(conn: sqlite3.Connection, config_id: int) -> bool:
    """Delete LDAP configuration"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM ldap_configs WHERE id = ?", (config_id,))
            return cursor.rowcount > 0
    except Exception:
        return False

def _log_ldap_sync(conn: sqlite3.Connection, config_id: int, sync_type: str, users_found: int, 
                  users_created: int, users_removed: int, status: str, error_message: str = None) -> None:
    """Log LDAP synchronization results"""
    try:
        sync_time = datetime.now().isoformat()
        with conn:
            conn.execute(
                """INSERT INTO ldap_sync_log (config_id, sync_type, users_found, users_created, users_removed, sync_time, status, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (config_id, sync_type, users_found, users_created, users_removed, sync_time, status, error_message)
            )
    except Exception:
        pass  # Don't fail the sync if logging fails

def _get_ldap_sync_logs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Get recent LDAP sync logs"""
    try:
        rows = conn.execute(
            """SELECT l.id, l.config_id, c.name, l.sync_type, l.users_found, l.users_created, l.users_removed, 
                      l.sync_time, l.status, l.error_message
               FROM ldap_sync_log l
               JOIN ldap_configs c ON l.config_id = c.id
               ORDER BY l.sync_time DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "config_id": row[1],
                "config_name": row[2],
                "sync_type": row[3],
                "users_found": row[4],
                "users_created": row[5],
                "users_removed": row[6],
                "sync_time": row[7],
                "status": row[8],
                "error_message": row[9]
            }
            for row in rows
        ]
    except Exception:
        return []

def _sync_ldap_users(conn: sqlite3.Connection) -> dict:
    """Synchronize users from all active LDAP configurations"""
    if not conn:
        return {"status": "error", "message": "Database not available"}
    
    try:
        # Get all active LDAP configurations
        configs = _get_all_ldap_configs(conn)
        active_configs = [c for c in configs if c['active']]
        
        if not active_configs:
            return {"status": "success", "message": "No active LDAP configurations found"}
        
        all_ldap_users = set()  # Use set to automatically handle duplicates
        sync_results = []
        
        for config in active_configs:
            try:
                # Connect to LDAP server
                from ldap3 import Server, Connection, ALL
                server = Server(config['server'], get_info=ALL)
                conn_ldap = Connection(server)
                
                if not conn_ldap.bind():
                    sync_results.append({
                        "config_name": config['name'],
                        "status": "error",
                        "message": f"Failed to bind to LDAP server: {config['server']}"
                    })
                    continue
                
                # Search for groups and their members
                search_filter = f"(objectClass=groupOfNames)"
                conn_ldap.search(
                    search_base=config['ldap_base_dn'],
                    search_filter=search_filter,
                    attributes=[config['ldap_member_attributes']]
                )
                
                config_users = set()
                for entry in conn_ldap.entries:
                    if hasattr(entry, config['ldap_member_attributes']):
                        members = getattr(entry, config['ldap_member_attributes'])
                        if members:
                            for member in members:
                                # Extract username using the user template
                                username = _extract_username_from_dn(member, config['user_template'])
                                if username:
                                    config_users.add(username)
                                    all_ldap_users.add(username)
                
                sync_results.append({
                    "config_name": config['name'],
                    "status": "success",
                    "users_found": len(config_users)
                })
                
                # Log the sync for this config
                _log_ldap_sync(conn, config['id'], 'group_sync', len(config_users), 0, 0, 'success')
                
            except Exception as e:
                sync_results.append({
                    "config_name": config['name'],
                    "status": "error",
                    "message": str(e)
                })
                _log_ldap_sync(conn, config['id'], 'group_sync', 0, 0, 0, 'error', str(e))
        
        # Now sync users with the database
        users_created = 0
        users_removed = 0
        
        # Get current users from database
        current_users = _get_all_users(conn)
        current_usernames = {user['username'] for user in current_users}
        
        # Create new users that are in LDAP but not in database
        for username in all_ldap_users:
            if username not in current_usernames:
                try:
                    # Create user with a dummy password (they'll authenticate via LDAP)
                    _create_user(conn, username, "ldap_user", role='user')
                    users_created += 1
                except Exception as e:
                    print(f"Failed to create user {username}: {e}")
        
        # Remove users that are in database but not in LDAP
        for user in current_users:
            if user['username'] not in all_ldap_users and user['username'] != 'admin':
                try:
                    _delete_user(conn, user['id'])
                    users_removed += 1
                except Exception as e:
                    print(f"Failed to remove user {user['username']}: {e}")
        
        return {
            "status": "success",
            "total_ldap_users": len(all_ldap_users),
            "users_created": users_created,
            "users_removed": users_removed,
            "config_results": sync_results
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _extract_username_from_dn(dn: str, user_template: str) -> str:
    """Extract username from LDAP DN using the user template"""
    try:
        # Simple template matching - can be enhanced for more complex patterns
        if '{username}' in user_template:
            # For templates like "uid={username},ou=users,dc=example,dc=com"
            # We need to extract the actual username from the DN
            # This is a simplified implementation
            parts = dn.split(',')
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    if key.strip() in ['uid', 'cn', 'sAMAccountName']:
                        return value.strip()
        return None
    except Exception:
        return None

def _start_ldap_sync_scheduler(conn: sqlite3.Connection) -> None:
    """Start the daily LDAP sync scheduler in a background thread"""
    def sync_worker():
        while True:
            try:
                # Run sync at 2 AM every day
                current_time = datetime.now()
                if current_time.hour == 2 and current_time.minute == 0:
                    print("Starting daily LDAP sync...")
                    sync_result = _sync_ldap_users(conn)
                    if sync_result["status"] == "success":
                        print(f"Daily LDAP sync completed: {sync_result.get('total_ldap_users', 0)} users found, {sync_result.get('users_created', 0)} created, {sync_result.get('users_removed', 0)} removed")
                    else:
                        print(f"Daily LDAP sync failed: {sync_result.get('message', 'Unknown error')}")
                
                # Sleep for 1 minute to avoid busy waiting
                time.sleep(60)
            except Exception as e:
                print(f"Error in LDAP sync scheduler: {e}")
                time.sleep(300)  # Sleep for 5 minutes on error
    
    if conn:
        sync_thread = threading.Thread(target=sync_worker, daemon=True)
        sync_thread.start()
        print("LDAP sync scheduler started (daily at 2 AM)")

def _assign_admin_privileges(conn: sqlite3.Connection, admin_users: list) -> None:
    """Assign admin privileges to users listed in admin_users configuration"""
    if not admin_users or not conn:
        return
    
    try:
        for username in admin_users:
            if not username or not isinstance(username, str):
                continue
                
            # Check if user exists
            user = _get_user_by_username(conn, username)
            if user:
                # Update user role to admin if not already admin
                if user['role'] != 'admin':
                    _update_user(conn, user['id'], role='admin')
                    print(f"ADMIN: Assigned admin privileges to existing user '{username}'")
            else:
                print(f"ADMIN: User '{username}' not found in database - will be assigned admin privileges on first login")
    except Exception as e:
        print(f"ADMIN: Warning: Failed to assign admin privileges: {e}")

def _delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """Delete a user from the database"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0
    except Exception:
        return False

def _authenticate_user(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    """Authenticate a user and update last_login"""
    user = _get_user_by_username(conn, username)
    if user and user['active'] and _verify_password(password, user['password_hash']):
        # Update last login timestamp
        _update_user(conn, user['id'], last_login=datetime.now().isoformat())
        # Remove sensitive information before returning
        del user['password_hash']
        return user
    return None

def get_current_feature_flags() -> dict:
    """Return current feature flags with SQLite values taking precedence.
    Falls back to in-memory defaults if DB is unavailable.
    """
    current = FEATURE_FLAGS.copy()
    if DB_CONN is not None:
        try:
            persisted = _load_feature_flags(DB_CONN)
            if persisted:
                # Persisted values override runtime defaults
                for k, v in persisted.items():
                    current[k] = bool(v)
        except Exception:
            pass
    return current

def get_current_websocket_config() -> dict:
    """Return current WebSocket configuration with SQLite values taking precedence.
    Falls back to in-memory defaults if DB is unavailable.
    """
    current = WEBSOCKET_CONFIG.copy()
    if DB_CONN is not None:
        try:
            persisted = _load_websocket_config(DB_CONN)
            if persisted:
                # Persisted values override runtime defaults
                for k, v in persisted.items():
                    current[k] = int(v)
        except Exception:
            pass
    return current

def is_feature_enabled(key: str, default: bool = False) -> bool:
    flags = get_current_feature_flags()
    return bool(flags.get(key, default))

class MMapFileHandler:
    """Efficient file handling using memory mapping for large files"""
    
    @staticmethod
    def should_use_mmap(file_size: int) -> bool:
        """Determine if mmap should be used based on file size"""
        return file_size >= MMAP_MIN_SIZE
    
    @staticmethod
    async def serve_file_chunk(file_path: str, start: int = 0, end: int = None, chunk_size: int = CHUNK_SIZE):
        """Serve file chunks using mmap for efficient memory usage"""
        try:
            file_size = os.path.getsize(file_path)
            
            if not MMapFileHandler.should_use_mmap(file_size):
                # Use traditional method for small files
                with open(file_path, 'rb') as f:
                    f.seek(start)
                    remaining = (end - start + 1) if end is not None else file_size - start
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        yield chunk
                        remaining -= len(chunk)
                return
            
            # Use mmap for large files
            with open(file_path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    actual_end = min(end or file_size - 1, file_size - 1)
                    current = start
                    
                    while current <= actual_end:
                        chunk_end = min(current + chunk_size, actual_end + 1)
                        yield mm[current:chunk_end]
                        current = chunk_end
                        
        except (OSError, ValueError) as e:
            # Fallback to traditional method on mmap errors
            with open(file_path, 'rb') as f:
                f.seek(start)
                remaining = (end - start + 1) if end is not None else file_size - start
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)
    
    @staticmethod
    def find_line_offsets(file_path: str, max_lines: int = None) -> list[int]:
        """Efficiently find line start offsets using mmap"""
        try:
            file_size = os.path.getsize(file_path)
            if not MMapFileHandler.should_use_mmap(file_size):
                # Use traditional method for small files
                offsets = [0]
                with open(file_path, 'rb') as f:
                    pos = 0
                    for line in f:
                        pos += len(line)
                        offsets.append(pos)
                        if max_lines and len(offsets) > max_lines:
                            break
                return offsets[:-1]  # Remove the last offset (EOF)
            
            # Use mmap for large files
            offsets = [0]
            with open(file_path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    pos = 0
                    while pos < len(mm):
                        newline_pos = mm.find(b'\n', pos)
                        if newline_pos == -1:
                            break
                        pos = newline_pos + 1
                        offsets.append(pos)
                        if max_lines and len(offsets) > max_lines:
                            break
            return offsets[:-1]
            
        except (OSError, ValueError):
            # Fallback to traditional method
            offsets = [0]
            with open(file_path, 'rb') as f:
                pos = 0
                for line in f:
                    pos += len(line)
                    offsets.append(pos)
                    if max_lines and len(offsets) > max_lines:
                        break
            return offsets[:-1]
    
    @staticmethod
    def search_in_file(file_path: str, search_term: str, max_results: int = 100) -> list[dict]:
        """Efficiently search for text in file using mmap"""
        results = []
        try:
            file_size = os.path.getsize(file_path)
            search_bytes = search_term.encode('utf-8')
            
            if not MMapFileHandler.should_use_mmap(file_size):
                # Use traditional method for small files
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line_num, line in enumerate(f, 1):
                        if search_term in line:
                            results.append({
                                "line_number": line_num,
                                "line_content": line.rstrip('\n'),
                                "match_positions": [i for i in range(len(line)) if line[i:].startswith(search_term)]
                            })
                            if len(results) >= max_results:
                                break
                return results
            
            # Use mmap for large files
            with open(file_path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    current_pos = 0
                    line_number = 1
                    line_start = 0
                    
                    while current_pos < len(mm) and len(results) < max_results:
                        newline_pos = mm.find(b'\n', current_pos)
                        if newline_pos == -1:
                            # Last line
                            line_bytes = mm[current_pos:]
                            if search_bytes in line_bytes:
                                line_content = line_bytes.decode('utf-8', errors='replace')
                                match_positions = []
                                start_pos = 0
                                while True:
                                    pos = line_content.find(search_term, start_pos)
                                    if pos == -1:
                                        break
                                    match_positions.append(pos)
                                    start_pos = pos + 1
                                results.append({
                                    "line_number": line_number,
                                    "line_content": line_content,
                                    "match_positions": match_positions
                                })
                            break
                        
                        line_bytes = mm[current_pos:newline_pos]
                        if search_bytes in line_bytes:
                            line_content = line_bytes.decode('utf-8', errors='replace')
                            match_positions = []
                            start_pos = 0
                            while True:
                                pos = line_content.find(search_term, start_pos)
                                if pos == -1:
                                    break
                                match_positions.append(pos)
                                start_pos = pos + 1
                            results.append({
                                "line_number": line_number,
                                "line_content": line_content,
                                "match_positions": match_positions
                            })
                        
                        current_pos = newline_pos + 1
                        line_number += 1
                        
        except (OSError, UnicodeDecodeError):
            # Fallback to traditional search
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                for line_num, line in enumerate(f, 1):
                    if search_term in line:
                        results.append({
                            "line_number": line_num,
                            "line_content": line.rstrip('\n'),
                            "match_positions": [i for i in range(len(line)) if line[i:].startswith(search_term)]
                        })
                        if len(results) >= max_results:
                            break
        
        return results

def get_files_in_directory(path="."):
    files = []
    for entry in os.scandir(path):
        stat = entry.stat()
        files.append({
            "name": entry.name,
            "is_dir": entry.is_dir(),
            "size_bytes": stat.st_size,
            "size_str": f"{stat.st_size / 1024:.2f} KB" if not entry.is_dir() else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            "modified_timestamp": int(stat.st_mtime)
        })
    return files

def get_file_icon(filename):
    ext = os.path.splitext(filename)[1].lower()
    
    # Special files by name (check first before extension)
    if filename.lower() in ["readme", "readme.md", "readme.txt"]:
        return "📖"
    elif filename.lower() in ["license", "licence", "copying"]:
        return "📜"
    elif filename.lower() in ["makefile", "cmake", "cmakelists.txt"]:
        return "🔨"
    elif filename.lower() in ["dockerfile", "docker-compose.yml", "docker-compose.yaml"]:
        return "🐳"
    elif filename.lower() in [".gitignore", ".gitattributes", ".gitmodules"]:
        return "🔧"
    elif filename.startswith(".env"):
        return "🔐"
    
    # Document files
    elif ext in [".txt", ".md", ".rst", ".text"]:
        return "📄"
    elif ext in [".doc", ".docx", ".rtf", ".odt"]:
        return "📝"
    elif ext in [".pdf"]:
        return "📕"
    elif ext in [".xls", ".xlsx", ".ods", ".csv"]:
        return "📊"
    elif ext in [".ppt", ".pptx", ".odp"]:
        return "📋"
    
    # Image files
    elif ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"]:
        return "🖼️"
    elif ext in [".svg", ".ico"]:
        return "🎨"
    elif ext in [".psd", ".ai", ".sketch"]:
        return "🎭"
    
    # Programming files
    elif ext in [".py", ".pyw"]:
        return "🐍💎"  # Enhanced Python source files with gem (precious/valuable)
    elif ext in [".pyc", ".pyo"]:
        return "🐍⚡"  # Compiled Python files with lightning (fast/optimized)
    elif ext in [".js", ".jsx", ".ts", ".tsx", ".mjs"]:
        return "🟨"
    elif ext in [".java", ".class", ".jar"]:
        return "☕"
    elif ext in [".cpp", ".cxx", ".cc", ".c", ".h", ".hpp"]:
        return "⚙️"
    elif ext in [".cs", ".vb", ".fs"]:
        return "🔷"
    elif ext in [".php", ".phtml"]:
        return "🐘"
    elif ext in [".rb", ".rake", ".gem"]:
        return "💎"
    elif ext in [".go"]:
        return "🐹"
    elif ext in [".rs"]:
        return "🦀"
    elif ext in [".swift"]:
        return "🦉"
    elif ext in [".kt", ".kts"]:
        return "🟣"
    elif ext in [".scala"]:
        return "🔴"
    elif ext in [".r", ".rmd"]:
        return "📊"
    elif ext in [".m", ".mm"]:
        return "🍎"
    elif ext in [".pl", ".pm"]:
        return "🐪"
    elif ext in [".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1"]:
        return "📟"
    elif ext in [".lua"]:
        return "🌙"
    elif ext in [".dart"]:
        return "🎯"
    
    # Web files
    elif ext in [".html", ".htm", ".xhtml"]:
        return "🌐"
    elif ext in [".css", ".scss", ".sass", ".less"]:
        return "🎨"
    elif ext in [".xml", ".xsl", ".xsd"]:
        return "📰"
    elif ext in [".json", ".jsonl"]:
        return "📋"
    elif ext in [".yaml", ".yml"]:
        return "📄"
    elif ext in [".toml", ".ini", ".cfg", ".conf"]:
        return "⚙️"
    
    # Archive files
    elif ext in [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".lz", ".lzma"]:
        return "🗜️"
    elif ext in [".deb", ".rpm", ".pkg", ".dmg", ".msi", ".exe"]:
        return "📦"
    
    # Video files
    elif ext in [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ogv", ".mpg", ".mpeg"]:
        return "🎬"
    
    # Audio files
    elif ext in [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".aiff"]:
        return "🎵"
    
    # Font files
    elif ext in [".ttf", ".otf", ".woff", ".woff2", ".eot"]:
        return "🔤"
    
    # Database files
    elif ext in [".db", ".sqlite", ".sqlite3", ".mdb", ".accdb"]:
        return "🗃️"
    
    # Log files
    elif ext in [".log", ".out", ".err"]:
        return "📜"
    
    # Data files
    elif ext in [".sql"]:
        return "🗄️"
    elif ext in [".parquet", ".avro", ".orc"]:
        return "📊"
    
    # Notebook files
    elif ext in [".ipynb"]:
        return "📓"
    
    
    # Default
    else:
        return "📦"

def is_video_file(filename):
    """Check if file is a supported video format"""
    ext = os.path.splitext(filename)[1].lower()
    video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.ogv'}
    return ext in video_extensions

def is_audio_file(filename):
    """Check if file is a supported audio format"""
    ext = os.path.splitext(filename)[1].lower()
    audio_extensions = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}
    return ext in audio_extensions


class FeatureFlagSocketHandler(tornado.websocket.WebSocketHandler):
    # Use connection manager with configurable limits for feature flags
    connection_manager = WebSocketConnectionManager("feature_flags", default_max_connections=50, default_idle_timeout=600)

    def open(self):
        if not self.connection_manager.add_connection(self):
            self.write_message(json.dumps({
                'error': 'Connection limit exceeded. Please try again later.'
            }))
            self.close(code=1013, reason="Connection limit exceeded")
            return
            
        # Load current feature flags from SQLite and send to client
        current_flags = self._get_current_feature_flags()
        self.write_message(json.dumps(current_flags))

    def on_close(self):
        self.connection_manager.remove_connection(self)

    def check_origin(self, origin):
        # Improved origin validation (Priority 2)
        return is_valid_websocket_origin(self, origin)

    def _get_current_feature_flags(self):
        """Get current feature flags, preferring SQLite data over in-memory."""
        if DB_CONN is not None:
            try:
                persisted_flags = _load_feature_flags(DB_CONN)
                if persisted_flags:
                    # Use persisted flags as base, merge with any runtime changes
                    current_flags = persisted_flags.copy()
                    # Update with any in-memory changes not yet persisted
                    for k, v in FEATURE_FLAGS.items():
                        current_flags[k] = bool(v)
                    return current_flags
            except Exception:
                pass
        # Fallback to in-memory flags
        return FEATURE_FLAGS.copy()

    @classmethod
    def send_updates(cls):
        """Send feature flag updates to all connected clients, using SQLite data."""
        # Get current flags from SQLite for consistency
        current_flags = {}
        if DB_CONN is not None:
            try:
                current_flags = _load_feature_flags(DB_CONN)
                if current_flags:
                    # Merge with any runtime changes
                    for k, v in FEATURE_FLAGS.items():
                        current_flags[k] = bool(v)
                else:
                    current_flags = FEATURE_FLAGS.copy()
            except Exception:
                current_flags = FEATURE_FLAGS.copy()
        else:
            current_flags = FEATURE_FLAGS.copy()

        # Use connection manager to broadcast updates
        cls.connection_manager.broadcast_message(json.dumps(current_flags))


class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        # Security headers
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-Frame-Options", "DENY")
        self.set_header("X-XSS-Protection", "1; mode=block")
        self.set_header("Referrer-Policy", "strict-origin-when-cross-origin")
        # Content Security Policy
        csp = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
        self.set_header("Content-Security-Policy", csp)

    def get_current_user(self) -> str | None:
        return self.get_secure_cookie("user")

    def get_current_admin(self) -> str | None:
        return self.get_secure_cookie("admin")
    
    def get_current_user_role(self) -> str | None:
        return self.get_secure_cookie("user_role")
    
    def is_admin_user(self) -> bool:
        """Check if current user has admin privileges (either admin token or admin role)"""
        user_role = self.get_current_user_role()
        if user_role:
            # Handle both bytes and string comparison
            if isinstance(user_role, bytes):
                user_role = user_role.decode('utf-8')
            return bool(self.get_current_admin() or user_role == "admin")
        return bool(self.get_current_admin())
    
    def get_display_username(self) -> str:
        """Get username for display purposes"""
        user = self.get_current_user()
        if user:
            # Decode from bytes if needed
            if isinstance(user, bytes):
                user = user.decode('utf-8')
            # Handle different user types
            if user == "token_authenticated":
                return "Admin (Token)"
            elif user == "authenticated":
                return "Admin (Token)"
            else:
                # Regular username - show role if available
                role = self.get_current_user_role()
                if role:
                    if isinstance(role, bytes):
                        role = role.decode('utf-8')
                    if role == "admin":
                        return f"{user} (Admin)"
                    else:
                        return f"{user} (User)"
                return user
        return "Guest"
    
    def write_error(self, status_code, **kwargs):
        # Generic error messages to prevent information disclosure
        error_messages = {
            400: "Bad Request",
            401: "Unauthorized", 
            403: "Forbidden",
            404: "Not Found",
            413: "Request Entity Too Large",
            500: "Internal Server Error"
        }
        self.render("error.html", 
                   status_code=status_code, 
                   message=error_messages.get(status_code, "Unknown Error"))
    
    def on_finish(self):
        """Log access with username"""
        # Get username for logging
        username = "anonymous"
        if self.current_user:
            if isinstance(self.current_user, bytes):
                username = self.current_user.decode('utf-8')
            else:
                username = str(self.current_user)
        
        # Get client IP
        client_ip = self.request.remote_ip
        
        # Get request details
        method = self.request.method
        uri = self.request.uri
        status = self.get_status()
        # user_agent = self.request.headers.get('User-Agent', 'Unknown')
        
        # Log access with username
        print(f"ACCESS: {client_ip} - {username} - {method} {uri} - {status}")

class RootHandler(BaseHandler):
    def get(self):
        self.redirect("/files/")

class LDAPLoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            self.redirect("/files/")
            return
        self.render("login.html", error=None, settings=self.settings)

    def post(self):
        # Input validation
        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "")
        
        if not username or not password:
            self.render("login.html", error="Username and password are required.", settings=self.settings)
            return
            
        # Basic input length validation
        if len(username) > 256 or len(password) > 256:
            self.render("login.html", error="Invalid input length.", settings=self.settings)
            return
        
        try:
            server = Server(self.settings['ldap_server'])
            conn = Connection(server, user=self.settings['ldap_user_template'].format(username=username), password=password, auto_bind=True)
            conn.search(search_base=self.settings['ldap_base_dn'],
             search_filter=self.settings['ldap_filter_template'].format(username=username),
              attributes=self.settings['ldap_attributes'])

            """
            attribute_map = [{"member":'cn=asdfasdf,dc=com,dc=io'}]
            """
            # authentication 
            
            # authorization logic - check LDAP attribute maps if configured
            ldap_attribute_map = self.settings.get('ldap_attribute_map', [])
            if ldap_attribute_map:
                # If attribute maps are configured, check authorization
                authorized = False
                for attribute_element in ldap_attribute_map:
                    for key, value in attribute_element.items():
                        try:
                            if value in conn.entries[0][key]:
                                authorized = True
                                break
                        except KeyError:
                            continue
                    if authorized:
                        break
                
                if not authorized:
                    self.render("login.html", error="Access denied. You do not have permission to access this system.", settings=self.settings)
                    return
            # If no attribute maps are configured, all LDAP users are authorized
            
            # Only create/update user in Aird's database after successful authorization
            if DB_CONN:
                existing_user = _get_user_by_username(DB_CONN, username)
                admin_users = self.settings.get('admin_users', [])
                is_admin_user = username in admin_users
                
                if not existing_user:
                    # Create new user in Aird's database for first-time LDAP login
                    try:
                        user_role = 'admin' if is_admin_user else 'user'
                        _create_user(DB_CONN, username, password, role=user_role)
                        print(f"LDAP: Created new user '{username}' from LDAP authentication with role '{user_role}'")
                    except Exception as e:
                        print(f"LDAP: Warning: Failed to create user '{username}' in database: {e}")
                        # Continue with login even if user creation fails
                else:
                    # Update last login timestamp and check for admin role assignment
                    try:
                        _update_user(DB_CONN, existing_user['id'], last_login=datetime.now().isoformat())
                        print(f"LDAP: Updated last login for user '{username}'")
                        
                        # Check if user should have admin privileges
                        if is_admin_user and existing_user['role'] != 'admin':
                            _update_user(DB_CONN, existing_user['id'], role='admin')
                            print(f"LDAP: Assigned admin privileges to user '{username}'")
                    except Exception as e:
                        print(f"LDAP: Warning: Failed to update user '{username}': {e}")
            
            # Successful authentication and authorization
            # Get user role for cookie setting
            user_role = 'admin' if is_admin_user else 'user'
            if existing_user:
                user_role = existing_user['role']
            
            self.set_secure_cookie("user", username, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
            self.set_secure_cookie("user_role", user_role, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
            self.redirect("/files/")
            return
        except Exception:
            # Generic error message to prevent information disclosure
            self.render("login.html", error="Authentication failed. Please check your credentials.", settings=self.settings)

class LoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            next_url = self.get_argument("next", "/files/")
            self.redirect(next_url)
            return
        next_url = self.get_argument("next", None)
        self.render("login.html", error=None, settings=self.settings, next_url=next_url)

    def post(self):
        # Check if it's username/password login or token login
        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "").strip()
        token = self.get_argument("token", "").strip()
        next_url = self.get_argument("next", "/files/")
        
        # Try username/password authentication first (if both provided)
        if username and password and DB_CONN is not None:
            # Input validation
            if len(username) > 256 or len(password) > 256:
                self.render("login.html", error="Invalid input length.", settings=self.settings, next_url=next_url)
                return
            
            try:
                user = _authenticate_user(DB_CONN, username, password)
                if user:
                    self.set_secure_cookie("user", username, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                    self.set_secure_cookie("user_role", user['role'], httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                    self.redirect(next_url)
                    return
                else:
                    self.render("login.html", error="Invalid username or password.", settings=self.settings, next_url=next_url)
                    return
            except Exception:
                self.render("login.html", error="Authentication failed. Please try again.", settings=self.settings, next_url=next_url)
                return
        
        # Fallback to token authentication
        if not token:
            if username or password:
                self.render("login.html", error="Invalid username or password.", settings=self.settings, next_url=next_url)
            else:
                self.render("login.html", error="Username/password or token is required.", settings=self.settings, next_url=next_url)
            return
            
        if len(token) > 512:  # Reasonable token length limit
            self.render("login.html", error="Invalid token.", settings=self.settings, next_url=next_url)
            return
            
        if token == ACCESS_TOKEN:
            self.set_secure_cookie("user", "token_authenticated", httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
            self.set_secure_cookie("user_role", "admin", httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")  # Token users get admin role
            self.redirect(next_url)
        else:
            self.render("login.html", error="Invalid credentials. Try again.", settings=self.settings, next_url=next_url)

class AdminLoginHandler(BaseHandler):
    def get(self):
        if self.is_admin_user():
            self.redirect("/admin")
            return
        self.render("admin_login.html", error=None)

    def post(self):
        # Check if it's username/password login or token login
        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "").strip()
        token = self.get_argument("token", "").strip()
        
        # Try username/password authentication first (if both provided)
        if username and password and DB_CONN is not None:
            # Input validation
            if len(username) > 256 or len(password) > 256:
                self.render("admin_login.html", error="Invalid input length.")
                return
            
            try:
                user = _authenticate_user(DB_CONN, username, password)
                if user and user['role'] == 'admin':
                    # Set both user and admin cookies for admin users
                    self.set_secure_cookie("user", username, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                    self.set_secure_cookie("user_role", user['role'], httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                    self.set_secure_cookie("admin", "authenticated", httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")  # Also set admin cookie
                    self.redirect("/admin")
                    return
                elif user and user['role'] != 'admin':
                    self.render("admin_login.html", error="Access denied. Admin privileges required.")
                    return
                else:
                    self.render("admin_login.html", error="Invalid username or password.")
                    return
            except Exception:
                self.render("admin_login.html", error="Authentication failed. Please try again.")
                return
        
        # Fallback to token authentication
        if not token:
            if username or password:
                self.render("admin_login.html", error="Invalid username or password.")
            else:
                self.render("admin_login.html", error="Username/password or token is required.")
            return
            
        if len(token) > 512:  # Reasonable token length limit
            self.render("admin_login.html", error="Invalid token.")
            return
            
        if token == ADMIN_TOKEN:
            self.set_secure_cookie("admin", "authenticated", httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
            self.redirect("/admin")
        else:
            self.render("admin_login.html", error="Invalid admin token.")

class LogoutHandler(BaseHandler):
    def get(self):
        # Clear both regular and admin auth cookies
        self.clear_cookie("user")
        self.clear_cookie("admin")
        # Redirect to login page
        self.redirect("/login")

class AdminHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not self.is_admin_user():
            self.redirect("/admin/login")
            return
        
        # Get Rust performance stats if available
        rust_stats = {}
        if RUST_AVAILABLE:
            try:
                from .rust_integration import performance_monitor
                rust_stats = performance_monitor.get_stats()
            except Exception:
                rust_stats = {"error": "Could not load performance stats"}
        
        # Get current feature flags from SQLite for consistency
        current_features = {}
        if DB_CONN is not None:
            try:
                persisted_flags = _load_feature_flags(DB_CONN)
                if persisted_flags:
                    current_features = persisted_flags.copy()
                    # Merge with any runtime changes
                    for k, v in FEATURE_FLAGS.items():
                        current_features[k] = bool(v)
                else:
                    current_features = FEATURE_FLAGS.copy()
            except Exception:
                current_features = FEATURE_FLAGS.copy()
        else:
            current_features = FEATURE_FLAGS.copy()

        # Get current WebSocket configuration
        current_websocket_config = get_current_websocket_config()
        
        # Check if LDAP is enabled
        ldap_enabled = self.settings.get('ldap_server') is not None
        
        self.render("admin.html",
                   features=current_features,
                   websocket_config=current_websocket_config,
                   rust_available=RUST_AVAILABLE,
                   rust_stats=rust_stats,
                   ldap_enabled=ldap_enabled)

    @tornado.web.authenticated
    def post(self):
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
        
        FEATURE_FLAGS["file_upload"] = self.get_argument("file_upload", "off") == "on"
        FEATURE_FLAGS["file_delete"] = self.get_argument("file_delete", "off") == "on"
        FEATURE_FLAGS["file_rename"] = self.get_argument("file_rename", "off") == "on"
        FEATURE_FLAGS["file_download"] = self.get_argument("file_download", "off") == "on"
        FEATURE_FLAGS["file_edit"] = self.get_argument("file_edit", "off") == "on"
        FEATURE_FLAGS["file_share"] = self.get_argument("file_share", "off") == "on"
        FEATURE_FLAGS["super_search"] = self.get_argument("super_search", "off") == "on"
        FEATURE_FLAGS["compression"] = self.get_argument("compression", "off") == "on"
        
        # Update WebSocket configuration
        websocket_config = {}
        try:
            # Parse and validate WebSocket settings
            websocket_config["feature_flags_max_connections"] = max(1, min(1000, int(self.get_argument("feature_flags_max_connections", "50"))))
            websocket_config["feature_flags_idle_timeout"] = max(30, min(7200, int(self.get_argument("feature_flags_idle_timeout", "600"))))
            websocket_config["file_streaming_max_connections"] = max(1, min(1000, int(self.get_argument("file_streaming_max_connections", "200"))))
            websocket_config["file_streaming_idle_timeout"] = max(30, min(7200, int(self.get_argument("file_streaming_idle_timeout", "300"))))
            websocket_config["search_max_connections"] = max(1, min(1000, int(self.get_argument("search_max_connections", "100"))))
            websocket_config["search_idle_timeout"] = max(30, min(7200, int(self.get_argument("search_idle_timeout", "180"))))
            
            # Update in-memory configuration
            WEBSOCKET_CONFIG.update(websocket_config)
            
        except (ValueError, TypeError):
            # If parsing fails, use current values
            pass
        
        # Persist both feature flags and WebSocket configuration
        try:
            if DB_CONN is not None:
                _save_feature_flags(DB_CONN, FEATURE_FLAGS)
                _save_websocket_config(DB_CONN, WEBSOCKET_CONFIG)
        except Exception:
            pass

        FeatureFlagSocketHandler.send_updates()
        self.redirect("/admin")

class WebSocketStatsHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Return WebSocket connection statistics"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        stats = {
            'feature_flags': FeatureFlagSocketHandler.connection_manager.get_stats(),
            'file_streaming': FileStreamHandler.connection_manager.get_stats(),
            'super_search': SuperSearchWebSocketHandler.connection_manager.get_stats(),
            'timestamp': time.time()
        }
        
        self.set_header('Content-Type', 'application/json')
        self.write(json.dumps(stats, indent=2))

class AdminUsersHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Display user management interface"""
        if not self.is_admin_user():
            self.redirect("/admin/login")
            return
            
        users = []
        if DB_CONN is not None:
            users = _get_all_users(DB_CONN)
            
        self.render("admin_users.html", users=users)

class UserCreateHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Show create user form"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        self.render("user_create.html", error=None)
    
    @tornado.web.authenticated
    def post(self):
        """Create a new user"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.render("user_create.html", error="Database not available")
            return
            
        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "").strip()
        role = self.get_argument("role", "user").strip()
        
        # Input validation
        if not username or not password:
            self.render("user_create.html", error="Username and password are required")
            return
            
        if len(username) < 3 or len(username) > 50:
            self.render("user_create.html", error="Username must be between 3 and 50 characters")
            return
            
        if len(password) < 6:
            self.render("user_create.html", error="Password must be at least 6 characters")
            return
            
        if role not in ['user', 'admin']:
            self.render("user_create.html", error="Invalid role")
            return
            
        # Check for valid username format (alphanumeric + underscore/hyphen)
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', username):
            self.render("user_create.html", error="Username can only contain letters, numbers, underscores, and hyphens")
            return
            
        try:
            _create_user(DB_CONN, username, password, role)
            self.redirect("/admin/users")
        except ValueError as e:
            self.render("user_create.html", error=str(e))
        except Exception as e:
            self.render("user_create.html", error="Failed to create user")

class UserEditHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, user_id):
        """Show edit user form"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
            
        try:
            user_id = int(user_id)
            # Get user by ID
            users = _get_all_users(DB_CONN)
            user = next((u for u in users if u['id'] == user_id), None)
            
            if not user:
                self.set_status(404)
                self.write("User not found")
                return
                
            self.render("user_edit.html", user=user, error=None)
        except ValueError:
            self.set_status(400)
            self.write("Invalid user ID")
    
    @tornado.web.authenticated
    def post(self, user_id):
        """Update user information"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
            
        try:
            user_id = int(user_id)
            # Get existing user
            users = _get_all_users(DB_CONN)
            user = next((u for u in users if u['id'] == user_id), None)
            
            if not user:
                self.set_status(404)
                self.write("User not found")
                return
            
            username = self.get_argument("username", "").strip()
            password = self.get_argument("password", "").strip()
            role = self.get_argument("role", "user").strip()
            active = self.get_argument("active", "off") == "on"
            
            # Input validation
            if not username:
                self.render("user_edit.html", user=user, error="Username is required")
                return
                
            if len(username) < 3 or len(username) > 50:
                self.render("user_edit.html", user=user, error="Username must be between 3 and 50 characters")
                return
                
            if password and len(password) < 6:
                self.render("user_edit.html", user=user, error="Password must be at least 6 characters")
                return
                
            if role not in ['user', 'admin']:
                self.render("user_edit.html", user=user, error="Invalid role")
                return
            
            # Check for valid username format
            import re
            if not re.match(r'^[a-zA-Z0-9_-]+$', username):
                self.render("user_edit.html", user=user, error="Username can only contain letters, numbers, underscores, and hyphens")
                return
            
            # Update user
            update_data = {
                'username': username,
                'role': role,
                'active': active
            }
            
            # Check if LDAP is enabled - disable password updates for LDAP users
            if password and self.settings.get('ldap_server'):
                self.render("user_edit.html", user=user, error="Password changes are not allowed for LDAP users. Please change the password through the LDAP directory.")
                return
            
            if password:  # Only update password if provided
                update_data['password'] = password
                
            if _update_user(DB_CONN, user_id, **update_data):
                self.redirect("/admin/users")
            else:
                self.render("user_edit.html", user=user, error="Failed to update user")
                
        except ValueError:
            self.set_status(400)
            self.write("Invalid user ID")
        except Exception as e:
            self.render("user_edit.html", user=user, error=f"Error updating user: {str(e)}")

class UserDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        """Delete a user"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
            
        try:
            user_id = int(self.get_argument("user_id", "0"))
            
            if user_id <= 0:
                self.set_status(400)
                self.write("Invalid user ID")
                return
                
            if _delete_user(DB_CONN, user_id):
                self.redirect("/admin/users")
            else:
                self.set_status(404)
                self.write("User not found")
                
        except ValueError:
            self.set_status(400)
            self.write("Invalid user ID")

class LDAPConfigHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Display LDAP configuration management interface"""
        if not self.is_admin_user():
            self.redirect("/admin/login")
            return
            
        configs = []
        sync_logs = []
        if DB_CONN is not None:
            configs = _get_all_ldap_configs(DB_CONN)
            sync_logs = _get_ldap_sync_logs(DB_CONN, limit=20)
            
        self.render("admin_ldap.html", configs=configs, sync_logs=sync_logs)

class LDAPConfigCreateHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Show create LDAP configuration form"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        self.render("ldap_config_create.html", error=None)
    
    @tornado.web.authenticated
    def post(self):
        """Create a new LDAP configuration"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.render("ldap_config_create.html", error="Database not available")
            return
            
        name = self.get_argument("name", "").strip()
        server = self.get_argument("server", "").strip()
        ldap_base_dn = self.get_argument("ldap_base_dn", "").strip()
        ldap_member_attributes = self.get_argument("ldap_member_attributes", "member").strip()
        user_template = self.get_argument("user_template", "").strip()
        
        # Input validation
        if not all([name, server, ldap_base_dn, user_template]):
            self.render("ldap_config_create.html", error="All fields are required")
            return
            
        if len(name) < 3 or len(name) > 50:
            self.render("ldap_config_create.html", error="Configuration name must be between 3 and 50 characters")
            return
            
        try:
            _create_ldap_config(DB_CONN, name, server, ldap_base_dn, ldap_member_attributes, user_template)
            self.redirect("/admin/ldap")
        except ValueError as e:
            self.render("ldap_config_create.html", error=str(e))
        except Exception as e:
            self.render("ldap_config_create.html", error=f"Error creating configuration: {str(e)}")

class LDAPConfigEditHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, config_id):
        """Show edit LDAP configuration form"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
            
        try:
            config_id = int(config_id)
            config = _get_ldap_config_by_id(DB_CONN, config_id)
            
            if not config:
                self.set_status(404)
                self.write("Configuration not found")
                return
                
            self.render("ldap_config_edit.html", config=config, error=None)
        except ValueError:
            self.write("Invalid configuration ID")
    
    @tornado.web.authenticated
    def post(self, config_id):
        """Update LDAP configuration"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
            
        try:
            config_id = int(config_id)
            config = _get_ldap_config_by_id(DB_CONN, config_id)
            
            if not config:
                self.set_status(404)
                self.write("Configuration not found")
                return
            
            name = self.get_argument("name", "").strip()
            server = self.get_argument("server", "").strip()
            ldap_base_dn = self.get_argument("ldap_base_dn", "").strip()
            ldap_member_attributes = self.get_argument("ldap_member_attributes", "member").strip()
            user_template = self.get_argument("user_template", "").strip()
            active = self.get_argument("active", "off") == "on"
            
            # Input validation
            if not all([name, server, ldap_base_dn, user_template]):
                self.render("ldap_config_edit.html", config=config, error="All fields are required")
                return
                
            if len(name) < 3 or len(name) > 50:
                self.render("ldap_config_edit.html", config=config, error="Configuration name must be between 3 and 50 characters")
                return
            
            # Update configuration
            if _update_ldap_config(DB_CONN, config_id, 
                                 name=name, server=server, ldap_base_dn=ldap_base_dn,
                                 ldap_member_attributes=ldap_member_attributes, 
                                 user_template=user_template, active=active):
                self.redirect("/admin/ldap")
            else:
                self.render("ldap_config_edit.html", config=config, error="Failed to update configuration")
                
        except ValueError:
            self.write("Invalid configuration ID")
        except Exception as e:
            self.render("ldap_config_edit.html", config=config, error=f"Error updating configuration: {str(e)}")

class LDAPConfigDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        """Delete LDAP configuration"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
            
        try:
            config_id = int(self.get_argument("config_id", "0"))
            
            if config_id <= 0:
                self.set_status(400)
                self.write("Invalid configuration ID")
                return
                
            if _delete_ldap_config(DB_CONN, config_id):
                self.redirect("/admin/ldap")
            else:
                self.set_status(404)
                self.write("Configuration not found")
                
        except ValueError:
            self.set_status(400)
            self.write("Invalid configuration ID")

class LDAPSyncHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        """Trigger LDAP user synchronization"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
        
        try:
            sync_result = _sync_ldap_users(DB_CONN)
            
            if sync_result["status"] == "success":
                self.write(json.dumps({
                    "status": "success",
                    "message": f"Sync completed. Found {sync_result.get('total_ldap_users', 0)} users, created {sync_result.get('users_created', 0)}, removed {sync_result.get('users_removed', 0)}"
                }))
            else:
                self.set_status(500)
                self.write(json.dumps({
                    "status": "error",
                    "message": sync_result.get("message", "Unknown error")
                }))
                
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({
                "status": "error",
                "message": str(e)
            }))

class ProfileHandler(BaseHandler):
    def _get_ldap_enabled(self):
        """Helper method to check if LDAP is enabled"""
        return self.settings.get('ldap_server') is not None
    
    @tornado.web.authenticated
    def get(self):
        """Show user profile page"""
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
            
        # Get current user info
        current_user = self.get_current_user()
        if not current_user:
            self.redirect("/login")
            return
            
        # Decode username if it's bytes
        if isinstance(current_user, bytes):
            current_user = current_user.decode('utf-8')
            
        # Skip profile for token-authenticated users
        if current_user in ["token_authenticated", "authenticated"]:
            self.render("profile.html", 
                       user=None, 
                       error="Profile not available for token-authenticated users.",
                       success=None,
                       ldap_enabled=self._get_ldap_enabled())
            return
            
        try:
            user = _get_user_by_username(DB_CONN, current_user)
            if not user:
                self.set_status(404)
                self.write("User not found")
                return
                
            # Check if LDAP is enabled
            ldap_enabled = self.settings.get('ldap_server') is not None
            
            self.render("profile.html", user=user, error=None, success=None, ldap_enabled=ldap_enabled)
        except Exception as e:
            logging.getLogger(__name__).exception(f"Error loading profile for user {current_user}: {str(e)}")
            self.set_status(500)
            self.write("Error loading profile")
    
    @tornado.web.authenticated
    def post(self):
        """Update user profile"""
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database not available")
            return
            
        # Get current user info
        current_user = self.get_current_user()
        if not current_user:
            self.redirect("/login")
            return
            
        # Decode username if it's bytes
        if isinstance(current_user, bytes):
            current_user = current_user.decode('utf-8')
            
        # Skip profile for token-authenticated users
        if current_user in ["token_authenticated", "authenticated"]:
            self.render("profile.html", 
                       user=None, 
                       error="Profile not available for token-authenticated users.",
                       success=None,
                       ldap_enabled=self._get_ldap_enabled())
            return
            
        try:
            user = _get_user_by_username(DB_CONN, current_user)
            if not user:
                self.set_status(404)
                self.write("User not found")
                return
            
            # Get form data
            action = self.get_argument("action", "")
            current_password = self.get_argument("current_password", "").strip()
            
            # Validate current password first
            if not current_password:
                self.render("profile.html", user=user, error="Current password is required.", success=None, ldap_enabled=self._get_ldap_enabled())
                return
                
            if not _verify_password(current_password, user['password_hash']):
                self.render("profile.html", user=user, error="Current password is incorrect.", success=None, ldap_enabled=self._get_ldap_enabled())
                return
            
            if action == "change_password":
                # Check if LDAP is enabled - disable password changes for LDAP users
                if self.settings.get('ldap_server'):
                    self.render("profile.html", user=user, error="Password changes are not allowed for LDAP users. Please change your password through your LDAP directory.", success=None, ldap_enabled=self._get_ldap_enabled())
                    return
                
                new_password = self.get_argument("new_password", "").strip()
                confirm_password = self.get_argument("confirm_password", "").strip()
                
                # Input validation
                if not new_password:
                    self.render("profile.html", user=user, error="New password is required.", success=None, ldap_enabled=self._get_ldap_enabled())
                    return
                    
                if len(new_password) < 6:
                    self.render("profile.html", user=user, error="Password must be at least 6 characters.", success=None, ldap_enabled=self._get_ldap_enabled())
                    return
                    
                if new_password != confirm_password:
                    self.render("profile.html", user=user, error="New passwords do not match.", success=None, ldap_enabled=self._get_ldap_enabled())
                    return
                
                # Update password
                if _update_user(DB_CONN, user['id'], password=new_password):
                    self.render("profile.html", user=user, error=None, success="Password updated successfully!", ldap_enabled=self._get_ldap_enabled())
                else:
                    self.render("profile.html", user=user, error="Failed to update password.", success=None, ldap_enabled=self._get_ldap_enabled())
            else:
                self.render("profile.html", user=user, error="Invalid action.", success=None, ldap_enabled=self._get_ldap_enabled())
                
        except Exception as e:
            logging.getLogger(__name__).exception(f"Error updating profile for user {current_user}: {str(e)}")
            self.render("profile.html", user=None, error=f"Error updating profile: {str(e)}", success=None, ldap_enabled=self._get_ldap_enabled())

def get_relative_path(path, root):
    if path.startswith(root):
        return os.path.relpath(path, root)
    return path

class MainHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, path):
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))

        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write("Forbidden")
            return

        if os.path.isdir(abspath):
            # Collect all shared paths from database
            all_shared_paths = set()
            if DB_CONN:
                all_shares = _get_all_shares(DB_CONN)
                for share in all_shares.values():
                    for p in share.get('paths', []):
                        all_shared_paths.add(p)

            # Use Rust-optimized directory scanning when available
            if RUST_AVAILABLE and HybridFileHandler:
                try:
                    files = HybridFileHandler.scan_directory(abspath)
                except Exception as e:
                    # Fallback to Python implementation on error
                    logger.warning(f"Rust directory scan failed, using Python fallback: {e}")
                    files = get_files_in_directory(abspath)
            else:
                files = get_files_in_directory(abspath)
            
            # Augment file data with shared status
            for file_info in files:
                full_path = join_path(path, file_info['name'])
                file_info['is_shared'] = full_path in all_shared_paths

            parent_path = os.path.dirname(path) if path else None
            # Use SQLite-backed flags for template
            flags_for_template = get_current_feature_flags()
            self.render(
                "browse.html", 
                current_path=path, 
                parent_path=parent_path, 
                files=files,
                join_path=join_path,
                get_file_icon=get_file_icon,
                features=flags_for_template,
                max_file_size=MAX_FILE_SIZE
            )
        elif os.path.isfile(abspath):
            filename = os.path.basename(abspath)
            if self.get_argument('download', None):
                if not FEATURE_FLAGS.get("file_download", True):
                    self.set_status(403)
                    self.write("File download is disabled.")
                    return

                self.set_header('Content-Disposition', f'attachment; filename="{filename}"')

                # Guess MIME type
                mime_type, _ = mimetypes.guess_type(abspath)
                mime_type = mime_type or "application/octet-stream"
                self.set_header('Content-Type', mime_type)

                # Check for compressible types
                if FEATURE_FLAGS.get("compression", True):
                    compressible_types = ['text/', 'application/json', 'application/javascript', 'application/xml']
                    if any(mime_type.startswith(prefix) for prefix in compressible_types):
                        self.set_header("Content-Encoding", "gzip")

                        # Use Rust-optimized compression when available
                        if RUST_AVAILABLE and HybridCompressionHandler:
                            try:
                                # Use async file reading to avoid blocking
                                async with aiofiles.open(abspath, 'rb') as f:
                                    file_data = await f.read()
                                # Compression happens in thread pool to avoid blocking
                                import concurrent.futures
                                with concurrent.futures.ThreadPoolExecutor() as executor:
                                    compressed_data = await asyncio.get_event_loop().run_in_executor(
                                        executor, lambda: HybridCompressionHandler.compress_data(file_data, level=6)
                                    )
                                self.write(compressed_data)
                                await self.flush()
                                return
                            except Exception as e:
                                logger.warning(f"Rust compression failed, using Python fallback: {e}")
                        
                        # Fallback to Python gzip compression with async I/O
                        def compress_file():
                            buffer = BytesIO()
                            with open(abspath, 'rb') as f_in, gzip.GzipFile(fileobj=buffer, mode='wb') as f_out:
                                shutil.copyfileobj(f_in, f_out)
                            return buffer.getvalue()
                        
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            compressed_data = await asyncio.get_event_loop().run_in_executor(
                                executor, compress_file
                            )

                        self.write(compressed_data)
                        await self.flush()
                        return

                # Use Rust-optimized file serving when available
                if RUST_AVAILABLE and HybridFileHandler:
                    async for chunk in HybridFileHandler.serve_file_chunk(abspath):
                        self.write(chunk)
                        await self.flush()
                else:
                    # Fallback to Python mmap implementation
                    async for chunk in MMapFileHandler.serve_file_chunk(abspath):
                        self.write(chunk)
                        await self.flush()
                return

            # File viewing (stream/filter/text)
            start_streaming = self.get_argument('stream', None) is not None
            if start_streaming:
                # Get filter parameter for HTTP streaming (supports complex expressions)
                filter_expr = self.get_argument('filter', None)
                filter_expression = None
                if filter_expr:
                    filter_expr = filter_expr.strip()
                    if filter_expr:
                        filter_expression = FilterExpression(filter_expr)
                
                self.set_header('Content-Type', 'text/plain; charset=utf-8')
                filter_msg = f" (filtered by '{filter_expr}')" if filter_expr else ""
                self.write(f"Streaming file: {filename}{filter_msg}\n\n")
                await self.flush()
                # Stream line-by-line using async file I/O
                async with aiofiles.open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                    async for raw in f:
                        # Avoid double spacing: strip one trailing newline and let browser render line breaks
                        line = raw[:-1] if raw.endswith('\n') else raw
                        # Apply complex filter if specified
                        if filter_expression is None or filter_expression.matches(line):
                            self.write(line + '\n')
                            await self.flush()
                return

            filter_substring = self.get_argument('filter', None)
            # Legacy param no longer used for inline editing, kept for compatibility
            _ = self.get_argument('edit', None)
            start_line = self.get_argument('start_line', None)
            end_line = self.get_argument('end_line', None)

            # Parse line range parameters with defaults and clamping
            try:
                start_line = int(start_line) if start_line is not None else 1
            except ValueError:
                start_line = 1
            if start_line < 1:
                start_line = 1

            # If no end_line is specified, show the entire file (None means no limit)
            # Only default to 100 if end_line is explicitly provided but invalid
            if end_line is not None:
                try:
                    end_line = int(end_line)
                except ValueError:
                    end_line = 100
            else:
                end_line = None  # No limit - show entire file
            
            # Ensure start_line <= end_line (only if end_line is specified)
            if end_line is not None and start_line > end_line:
                start_line = end_line
            
            # Use mmap for efficient large file viewing
            file_content_parts: list[str] = []
            lines_items: list[dict] = []
            total_lines = 0
            display_index = 0  # used when filtering; numbering restarts at 1
            reached_EOF = False
            
            try:
                file_size = os.path.getsize(abspath)
                use_mmap = MMapFileHandler.should_use_mmap(file_size)
                
                if use_mmap:
                    # Use mmap for large files - more efficient line processing
                    with open(abspath, 'rb') as f:
                        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                            current_pos = 0
                            line_start = 0
                            
                            while current_pos < len(mm):
                                newline_pos = mm.find(b'\n', current_pos)
                                if newline_pos == -1:
                                    # Last line without newline
                                    if current_pos < len(mm):
                                        line_bytes = mm[current_pos:len(mm)]
                                        line = line_bytes.decode('utf-8', errors='replace')
                                        total_lines += 1
                                        if total_lines >= start_line and (end_line is None or total_lines <= end_line):
                                            if not filter_substring or filter_substring in line:
                                                if filter_substring:
                                                    display_index += 1
                                                    lines_items.append({"n": display_index, "text": line})
                                                else:
                                                    lines_items.append({"n": total_lines, "text": line})
                                                file_content_parts.append(line + '\n')
                                    reached_EOF = True
                                    break
                                
                                line_bytes = mm[current_pos:newline_pos]
                                line = line_bytes.decode('utf-8', errors='replace')
                                total_lines += 1
                                current_pos = newline_pos + 1
                                
                                if total_lines < start_line:
                                    continue
                                if end_line is not None and total_lines > end_line:
                                    break
                                    
                                if not filter_substring or filter_substring in line:
                                    if filter_substring:
                                        display_index += 1
                                        lines_items.append({"n": display_index, "text": line})
                                    else:
                                        lines_items.append({"n": total_lines, "text": line})
                                    file_content_parts.append(line + '\n')
                            else:
                                reached_EOF = True
                else:
                    # Use traditional method for small files
                    with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                        for line in f:
                            total_lines += 1
                            if total_lines < start_line:
                                continue
                            if end_line is not None and total_lines > end_line:
                                break
                            if filter_substring:
                                if filter_substring in line:
                                    display_index += 1
                                    file_content_parts.append(line)
                                    lines_items.append({
                                        "n": display_index,
                                        "text": line.rstrip('\n')
                                    })
                            else:
                                file_content_parts.append(line)
                                lines_items.append({
                                    "n": total_lines,
                                    "text": line.rstrip('\n')
                                })
                        else:
                            reached_EOF = True
                            
            except (OSError, UnicodeDecodeError):
                # Fallback to traditional method on any errors
                with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        total_lines += 1
                        if total_lines < start_line:
                            continue
                        if end_line is not None and total_lines > end_line:
                            break
                        if filter_substring:
                            if filter_substring in line:
                                display_index += 1
                                file_content_parts.append(line)
                                lines_items.append({
                                    "n": display_index,
                                    "text": line.rstrip('\n')
                                })
                        else:
                            file_content_parts.append(line)
                            lines_items.append({
                                "n": total_lines,
                                "text": line.rstrip('\n')
                            })
                    else:
                        reached_EOF = True
            # When filtering, restart numbering from 1 in the rendered view
            if filter_substring:
                start_line = 1
            file_content = ''.join(file_content_parts)

            # Escape user-controlled values to prevent XSS in inline HTML (Priority 1)
            safe_path = tornado_escape.xhtml_escape(path)
            safe_filter = tornado_escape.xhtml_escape(filter_substring) if filter_substring else ''
            filter_html = f'''
            <form method="get" style="margin-bottom:10px;">
                <input type="hidden" name="path" value="{safe_path}">
                <input type="text" name="filter" placeholder="Filter lines..." value="{safe_filter}" style="width:200px;">
                <button type="submit">Apply Filter</button>
            </form>
            '''
            flags_for_template = get_current_feature_flags()
            self.render("file.html", 
                      filename=filename, 
                      path=path, 
                      file_content=file_content, 
                      filter_html=filter_html, 
                      features=flags_for_template,
                      start_line=start_line,
                      end_line=end_line,
                      lines=lines_items,
                      open_editor=False,
                      full_file_content="",
                      reached_EOF=reached_EOF)
        else:
            self.set_status(404)
            self.write("File not found")


class FileStreamHandler(tornado.websocket.WebSocketHandler):
    # Use connection manager with configurable limits for file streaming
    connection_manager = WebSocketConnectionManager("file_streaming", default_max_connections=200, default_idle_timeout=300)
    
    def get_current_user(self) -> str | None:
        return self.get_secure_cookie("user")

    def check_origin(self, origin):
        # Improved origin validation (Priority 2)
        return is_valid_websocket_origin(self, origin)

    async def open(self, path):
        if not self.current_user:
            self.close()
            return
            
        if not self.connection_manager.add_connection(self):
            self.write_message("Connection limit exceeded. Please try again later.")
            self.close(code=1013, reason="Connection limit exceeded")
            return

        path = path.lstrip('/')
        self.file_path = os.path.abspath(os.path.join(ROOT_DIR, path))
        # Enforce root restriction (Priority 1)
        if not is_within_root(self.file_path, ROOT_DIR):
            self.write_message("Forbidden path")
            self.close()
            return
        self.running = True
        # Number of tail lines to send on connect
        try:
            n_param = self.get_query_argument('n', default='100')
            self.tail_n = int(n_param)
            if self.tail_n < 1:
                self.tail_n = 100
        except Exception:
            self.tail_n = 100
            
        # Filter expression for line filtering (supports AND/OR logic)
        filter_expr = self.get_query_argument('filter', default=None)
        if filter_expr:
            filter_expr = filter_expr.strip()
            if filter_expr:
                self.filter_expression = FilterExpression(filter_expr)
            else:
                self.filter_expression = None
        else:
            self.filter_expression = None
        if not os.path.isfile(self.file_path):
            self.write_message(f"File not found: {self.file_path}")
            self.close()
            return

        try:
            # Use async file reading to avoid blocking event loop
            async with aiofiles.open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                last_n_lines = deque()
                async for line in f:
                    last_n_lines.append(line)
                    if len(last_n_lines) > self.tail_n:
                        last_n_lines.popleft()
            if last_n_lines:
                for line in last_n_lines:
                    # Apply complex filter if specified
                    if self.filter_expression is None or self.filter_expression.matches(line):
                        self.write_message(line)
        except Exception as e:
            self.write_message(f"Error reading file history: {e}")

        try:
            # Note: For continuous file monitoring, we still need regular file operations
            # as aiofiles doesn't support continuous monitoring well
            # This is kept synchronous but runs infrequently (every 100ms)
            self.file = open(self.file_path, 'r', encoding='utf-8', errors='replace')
            self.file.seek(0, os.SEEK_END)
        except Exception as e:
            self.write_message(f"Error opening file for streaming: {e}")
            self.close()
            return
        self.loop = tornado.ioloop.IOLoop.current()
        # Stream near real-time
        self.periodic = tornado.ioloop.PeriodicCallback(self.send_new_lines, 100)
        self.periodic.start()

    async def send_new_lines(self):
        if not self.running:
            return
        where = self.file.tell()
        line = self.file.readline()
        while line:
            # Apply complex filter if specified
            if self.filter_expression is None or self.filter_expression.matches(line):
                self.write_message(line)
                # Update activity for each message sent
                self.connection_manager.update_activity(self)
            where = self.file.tell()
            line = self.file.readline()
        self.file.seek(where)

    def on_close(self):
        self.running = False
        self.connection_manager.remove_connection(self)
        if hasattr(self, 'periodic'):
            self.periodic.stop()
        if hasattr(self, 'file'):
            self.file.close()

@tornado.web.stream_request_body
class UploadHandler(BaseHandler):
    async def prepare(self):
        # Defaults for safety
        self._reject: bool = False
        self._reject_reason: str | None = None
        self._temp_path: str | None = None
        self._aiofile = None
        self._buffer = deque()
        self._writer_task = None
        self._writing: bool = False
        self._moved: bool = False
        self._bytes_received: int = 0
        self._too_large: bool = False

        # Feature flag check (using SQLite-backed flags)
        # Deferred to post() for clear response, but avoid heavy work if disabled
        if not is_feature_enabled("file_upload", True):
            self._reject = True
            self._reject_reason = "File upload is disabled."
            return

        # Read and decode headers provided by client
        self.upload_dir = unquote(self.request.headers.get("X-Upload-Dir", ""))
        self.filename = unquote(self.request.headers.get("X-Upload-Filename", ""))

        # Basic validation
        if not self.filename:
            self._reject = True
            self._reject_reason = "Missing X-Upload-Filename header"
            return

        # Create temporary file for streamed writes
        fd, self._temp_path = tempfile.mkstemp(prefix="aird_upload_")
        # Close the low-level fd; we'll use aiofiles on the path
        os.close(fd)
        self._aiofile = await aiofiles.open(self._temp_path, "wb")

    def data_received(self, chunk: bytes) -> None:
        if self._reject:
            return
        # Track size to enforce limit at the end
        self._bytes_received += len(chunk)
        if self._bytes_received > MAX_FILE_SIZE:
            self._too_large = True
            # We still accept the stream but won't persist it
            return

        # Queue the chunk and ensure a writer task is draining
        self._buffer.append(chunk)
        if not self._writing:
            self._writing = True
            self._writer_task = asyncio.create_task(self._drain_buffer())

    async def _drain_buffer(self) -> None:
        try:
            while self._buffer:
                data = self._buffer.popleft()
                await self._aiofile.write(data)
            await self._aiofile.flush()
        finally:
            self._writing = False

    @tornado.web.authenticated
    async def post(self):
        # If uploads disabled, return now
        if not is_feature_enabled("file_upload", True):
            self.set_status(403)
            self.write("File upload is disabled.")
            return

        # If we rejected in prepare (bad/missing headers), report
        if self._reject:
            self.set_status(400)
            self.write(self._reject_reason or "Bad request")
            return

        # Wait for any in-flight writes to complete
        if self._writer_task is not None:
            try:
                await self._writer_task
            except Exception:
                pass

        # Close file to flush buffers
        if self._aiofile is not None:
            try:
                await self._aiofile.close()
            except Exception:
                pass

        # Enforce size limit
        if self._too_large:
            self.set_status(413)
            self.write("File too large")
            return

        # Enhanced path validation
        safe_dir_abs = os.path.realpath(os.path.join(ROOT_DIR, self.upload_dir.strip("/")))
        if not is_within_root(safe_dir_abs, ROOT_DIR):
            self.set_status(403)
            self.write("Forbidden path")
            return

        # Validate filename more strictly
        safe_filename = os.path.basename(self.filename)
        if not safe_filename or safe_filename in ['.', '..']:
            self.set_status(400)
            self.write("Invalid filename")
            return
            
        # Enforce allowed extensions (whitelist)
        file_ext = os.path.splitext(safe_filename)[1].lower()
        if file_ext not in ALLOWED_UPLOAD_EXTENSIONS:
            self.set_status(415)
            self.write("Unsupported file type")
            return
            
        # Validate filename length
        if len(safe_filename) > 255:
            self.set_status(400)
            self.write("Filename too long")
            return

        final_path_abs = os.path.realpath(os.path.join(safe_dir_abs, safe_filename))
        if not is_within_root(final_path_abs, safe_dir_abs):
            self.set_status(403)
            self.write("Forbidden path")
            return

        os.makedirs(os.path.dirname(final_path_abs), exist_ok=True)

        try:
            shutil.move(self._temp_path, final_path_abs)
            self._moved = True
        except Exception as e:
            self.set_status(500)
            self.write(f"Failed to save upload: {e}")
            return

        self.set_status(200)
        self.write("Upload successful")

    def on_finish(self) -> None:
        # Clean up temp file on failures
        try:
            if getattr(self, "_temp_path", None) and not getattr(self, "_moved", False):
                if os.path.exists(self._temp_path):
                    try:
                        os.remove(self._temp_path)
                    except Exception:
                        pass
        except Exception:
            pass

class DeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_delete", True):
            self.set_status(403)
            self.write("File delete is disabled.")
            return

        path = self.get_argument("path", "")
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        root = ROOT_DIR
        if not is_within_root(abspath, root):
            self.set_status(403)
            self.write("Forbidden")
            return
        if os.path.isdir(abspath):
            shutil.rmtree(abspath)
        elif os.path.isfile(abspath):
            os.remove(abspath)
        parent = os.path.dirname(path)
        self.redirect("/files/" + parent if parent else "/files/")

class RenameHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_rename", True):
            self.set_status(403)
            self.write("File rename is disabled.")
            return

        path = self.get_argument("path", "").strip()
        new_name = self.get_argument("new_name", "").strip()
        
        # Input validation
        if not path or not new_name:
            self.set_status(400)
            self.write("Path and new name are required.")
            return
            
        # Validate new filename
        if new_name in ['.', '..'] or '/' in new_name or '\\' in new_name:
            self.set_status(400)
            self.write("Invalid filename.")
            return
            
        if len(new_name) > 255:
            self.set_status(400)
            self.write("Filename too long.")
            return
        
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        new_abspath = os.path.abspath(os.path.join(ROOT_DIR, os.path.dirname(path), new_name))
        root = ROOT_DIR
        if not (is_within_root(abspath, root) and is_within_root(new_abspath, root)):
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if not os.path.exists(abspath):
            self.set_status(404)
            self.write("File not found")
            return
            
        try:
            os.rename(abspath, new_abspath)
        except OSError:
            self.set_status(500)
            self.write("Rename failed")
            return
            
        parent = os.path.dirname(path)
        self.redirect("/files/" + parent if parent else "/files/")


class EditHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_edit", True):
            self.set_status(403)
            self.write("File editing is disabled.")
            return

        # Accept both JSON and form-encoded bodies
        content_type = self.request.headers.get("Content-Type", "")
        path = ""
        content = ""
        if content_type.startswith("application/json"):
            try:
                data = json.loads(self.request.body.decode("utf-8", errors="replace") or "{}")
                path = data.get("path", "")
                content = data.get("content", "")
            except Exception:
                self.set_status(400)
                self.write("Invalid JSON body")
                return
        else:
            path = self.get_argument("path", "")
            content = self.get_argument("content", "")
        
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        
        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if not os.path.isfile(abspath):
            self.set_status(404)
            self.write("File not found")
            return

        try:
            # Safe write: write to temp file in same directory then replace atomically
            directory_name = os.path.dirname(abspath)
            os.makedirs(directory_name, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, dir=directory_name) as tmp:
                tmp.write(content)
                temp_path = tmp.name
            os.replace(temp_path, abspath)
            self.set_status(200)
            # Respond JSON if requested
            if self.request.headers.get('Accept') == 'application/json':
                self.write({"ok": True})
            else:
                self.write("File saved successfully.")
        except Exception as e:
            self.set_status(500)
            self.write(f"Error saving file: {e}")

class EditViewHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, path):
        if not is_feature_enabled("file_edit", True):
            self.set_status(403)
            self.write("File editing is disabled.")
            return

        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write("Forbidden")
            return
        if not os.path.isfile(abspath):
            self.set_status(404)
            self.write("File not found")
            return

        # Prevent loading extremely large files into memory in the editor
        try:
            file_size = os.path.getsize(abspath)
        except OSError:
            file_size = 0
        if file_size > MAX_READABLE_FILE_SIZE:
            self.set_status(413)
            self.write(f"File too large to edit in browser. Size: {file_size} bytes (limit {MAX_READABLE_FILE_SIZE} bytes)")
            return

        filename = os.path.basename(abspath)
        
        # Use async file loading to prevent blocking event loop
        try:
            file_size = os.path.getsize(abspath)
            if MMapFileHandler.should_use_mmap(file_size):
                # For large files, still use mmap but in a thread to avoid blocking
                import asyncio
                import concurrent.futures
                
                def read_mmap():
                    with open(abspath, 'rb') as f:
                        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                            return mm[:].decode('utf-8', errors='replace')
                
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    full_file_content = await asyncio.get_event_loop().run_in_executor(
                        executor, read_mmap
                    )
            else:
                # Use aiofiles for small files
                async with aiofiles.open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                    full_file_content = await f.read()
        except (OSError, UnicodeDecodeError):
            # Fallback to async read
            async with aiofiles.open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                full_file_content = await f.read()
                
        total_lines = full_file_content.count('\n') + 1 if full_file_content else 0

        self.render(
            "edit.html",
            filename=filename,
            path=path,
            full_file_content=full_file_content,
            total_lines=total_lines,
            features=get_current_feature_flags(),
        )

class FileListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, path):
        self.set_header("Content-Type", "application/json")
        
        # Normalize path
        path = path.strip('/')
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        
        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return

        if not os.path.isdir(abspath):
            self.set_status(404)
            self.write({"error": "Directory not found"})
            return

        try:
            # Use Rust-optimized directory scanning when available
            if RUST_AVAILABLE and HybridFileHandler:
                try:
                    files = HybridFileHandler.scan_directory(abspath)
                except Exception as e:
                    logger.warning(f"Rust directory scan failed, using Python fallback: {e}")
                    files = get_files_in_directory(abspath)
            else:
                files = get_files_in_directory(abspath)
            
            # Collect all shared paths from database
            all_shared_paths = set()
            if DB_CONN:
                all_shares = _get_all_shares(DB_CONN)
                for share in all_shares.values():
                    for p in share.get('paths', []):
                        all_shared_paths.add(p)
            
            # Augment file data with shared status
            for file_info in files:
                full_path = join_path(path, file_info['name'])
                file_info['is_shared'] = full_path in all_shared_paths
                
            result = {
                "path": path,
                "files": [
                    {
                        "name": f["name"],
                        "is_dir": f["is_dir"],
                        "size_str": f.get("size_str", "-"),
                        "modified": f.get("modified", "-"),
                        "is_shared": f.get("is_shared", False)
                    }
                    for f in files
                ]
            }
            self.write(result)
        except Exception:
            self.set_status(500)
            self.write({"error": "Internal server error"})

class ShareFilesHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write("File sharing is disabled")
            return
        # Just render the template - files will be loaded on-the-fly via JavaScript
        # Pass empty dict since shares are fetched via API
        self.render("share.html", shares={})

class ShareCreateHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
        try:
            data = json.loads(self.request.body or b'{}')
            paths = data.get('paths', [])
            allowed_users = data.get('allowed_users', [])
            share_type = data.get('share_type', 'static')  # Default to static for backward compatibility
            allow_list = data.get('allow_list', [])
            avoid_list = data.get('avoid_list', [])
            disable_token = data.get('disable_token', False)
            expiry_date = data.get('expiry_date', None)
            
            valid_paths = []
            dynamic_folders = []  # Store folders for dynamic shares
            
            for p in paths:
                ap = os.path.abspath(os.path.join(ROOT_DIR, p))
                if not is_within_root(ap, ROOT_DIR):
                    continue
                    
                if os.path.isfile(ap):
                    # It's a file, add it directly
                    valid_paths.append(p)
                elif os.path.isdir(ap):
                    if share_type == 'dynamic':
                        # For dynamic shares, store the folder path
                        dynamic_folders.append(p)
                        logging.debug(f"Added dynamic folder: {p}")
                    else:
                        # For static shares, recursively get all files
                        try:
                            all_files = _get_all_files_recursive(ap, p)
                            valid_paths.extend(all_files)
                            logging.debug(f"Added {len(all_files)} files from directory: {p}")
                        except Exception as e:
                            logging.error(f"Error scanning directory {p}: {e}")
                            continue
                        
            if share_type == 'dynamic':
                if not dynamic_folders:
                    self.set_status(400)
                    self.write({"error": "No valid directories for dynamic share"})
                    return
                # For dynamic shares, store the folder paths
                valid_paths = dynamic_folders
            else:
                if not valid_paths:
                    self.set_status(400)
                    self.write({"error": "No valid files or directories"})
                    return
                    
            sid = secrets.token_urlsafe(24)  # Increase entropy to reduce guessing risk (Priority 2)
            secret_token = secrets.token_urlsafe(32) if not disable_token else None  # Generate secret token only if not disabled
            created = datetime.utcnow().isoformat()
            
            # Ensure database connection
            global DB_CONN
            if DB_CONN is None:
                logging.warning("Database connection is None, attempting to reinitialize...")
                try:
                    DB_PATH = os.path.join(os.path.expanduser('~'), '.local', 'aird', 'aird.sqlite3')
                    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
                    DB_CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
                    _init_db(DB_CONN)
                    logging.info(f"Reinitialized database connection: {DB_CONN}")
                except Exception as reconnect_error:
                    logging.error(f"Failed to reconnect to database: {reconnect_error}")
                    self.set_status(500)
                    self.write({"error": "Database connection failed"})
                    return
            
            # Persist directly to database
            success = _insert_share(DB_CONN, sid, created, valid_paths, allowed_users if allowed_users else None, secret_token, share_type, allow_list if allow_list else None, avoid_list if avoid_list else None, expiry_date)
            if success:
                logging.info(f"Share {sid} created successfully in database")
                response_data = {"id": sid, "url": f"/shared/{sid}"}
                if not disable_token:
                    response_data["secret_token"] = secret_token
                self.write(response_data)
            else:
                logging.error(f"Failed to create share {sid} in database")
                self.set_status(500)
                self.write({"error": "Failed to create share"})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class ShareUpdateHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
        try:
            data = json.loads(self.request.body or b'{}')
            share_id = data.get('share_id')
            share_type = data.get('share_type', 'static')
            disable_token = data.get('disable_token', False)
            allow_list = data.get('allow_list', [])
            avoid_list = data.get('avoid_list', [])
            expiry_date = data.get('expiry_date', None)
            
            if not share_id:
                self.set_status(400)
                self.write({"error": "Share ID is required"})
                return
            
            # Ensure database connection
            global DB_CONN
            if DB_CONN is None:
                self.set_status(500)
                self.write({"error": "Database connection unavailable"})
                return
            
            # Get existing share
            share = _get_share_by_id(DB_CONN, share_id)
            if not share:
                self.set_status(404)
                self.write({"error": "Share not found"})
                return
            
            # Update share parameters
            logging.debug(f"Updating share {share_id} with parameters:")
            logging.debug(f"  - share_type: {share_type}")
            logging.debug(f"  - disable_token: {disable_token}")
            logging.debug(f"  - allow_list: {allow_list}")
            logging.debug(f"  - avoid_list: {avoid_list}")
            logging.debug(f"Database connection: {'available' if DB_CONN else 'None'}")
            
            success = _update_share(DB_CONN, share_id, share_type, disable_token, allow_list, avoid_list, None, expiry_date)
            if success:
                logging.debug(f"Share {share_id} updated successfully")
                
                # Verify the update by reading the share back
                updated_share = _get_share_by_id(DB_CONN, share_id)
                if updated_share:
                    logging.debug(f"Verification - Share {share_id} after update:")
                    logging.debug(f"  - share_type: {updated_share.get('share_type')}")
                    logging.debug(f"  - secret_token: {'present' if updated_share.get('secret_token') else 'none'}")
                    logging.debug(f"  - allow_list: {updated_share.get('allow_list')}")
                    logging.debug(f"  - avoid_list: {updated_share.get('avoid_list')}")
                
                response_data = {"success": True, "message": "Share updated successfully"}
                
                # If a new token was generated, include it in the response
                if disable_token is False and updated_share and updated_share.get('secret_token'):
                    response_data["new_token"] = updated_share.get('secret_token')
                    logging.debug(f"Returning new token to frontend")
                
                self.write(response_data)
            else:
                logging.error(f"Failed to update share {share_id}")
                self.set_status(500)
                self.write({"error": "Failed to update share"})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class ShareRevokeHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
        sid = self.get_argument('id', '')
        
        # Ensure database connection
        global DB_CONN
        if DB_CONN is None:
            self.set_status(500)
            self.write({"error": "Database connection unavailable"})
            return
            
        # Delete from database
        try:
            _delete_share(DB_CONN, sid)
            print(f"Share {sid} deleted from database")
        except Exception as e:
            print(f"Failed to delete share {sid}: {e}")
            
        if self.request.headers.get('Accept') == 'application/json':
            self.write({'ok': True})
            return
        self.redirect('/share')

class ShareListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return

        # Ensure database connection
        global DB_CONN
        if DB_CONN is None:
            self.set_status(500)
            self.write({"error": "Database connection unavailable"})
            return
            
        # Get all shares from database
        shares = _get_all_shares(DB_CONN)
        self.write({"shares": shares})

class ShareUpdateHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        """Update share access list"""
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return

        try:
            data = json.loads(self.request.body or b'{}')
            share_id = data.get('share_id')
            allowed_users = data.get('allowed_users', [])
            remove_files = data.get('remove_files', [])
            paths = data.get('paths')  # New: support for updating entire paths list
            disable_token = data.get('disable_token')
            allow_list = data.get('allow_list')
            avoid_list = data.get('avoid_list')
            if not share_id:
                self.set_status(400)
                self.write({"error": "Share ID is required"})
                return

            # Ensure database connection
            global DB_CONN
            if DB_CONN is None:
                self.set_status(500)
                self.write({"error": "Database connection unavailable"})
                return
                
            # Get current share data from database
            share_data = _get_share_by_id(DB_CONN, share_id)
            if not share_data:
                self.set_status(404)
                self.write({"error": "Share not found"})
                return

            # Prepare update data
            update_fields = {}
            
            # Handle file removal
            if remove_files:
                current_paths = share_data.get('paths', [])
                updated_paths = [p for p in current_paths if p not in remove_files]
                update_fields['paths'] = updated_paths
                print(f"Removing files {remove_files} from share {share_id}")
            
            # Handle complete paths update (for adding files)
            if paths is not None:
                update_fields['paths'] = paths
                print(f"Updating paths for share {share_id}: {paths}")

            # Update allowed users if provided
            if allowed_users is not None:
                update_fields['allowed_users'] = allowed_users if allowed_users else None
            

            if disable_token is True:
                update_fields['secret_token'] = None
                update_fields['disable_token'] = True
            elif disable_token is False:
                if share_data['secret_token'] is None:
                    update_fields['secret_token'] = secrets.token_urlsafe(32)
                else:
                    update_fields['secret_token'] = share_data['secret_token']
                update_fields['disable_token'] = False


            if allow_list is not None:
                update_fields['allow_list'] = allow_list
            if avoid_list is not None:
                update_fields['avoid_list'] = avoid_list
            
            # Update database
            if update_fields:
                db_success = _update_share(DB_CONN, share_id, **update_fields)
                if db_success:
                    print(f"Successfully updated share {share_id} in database")
                else:
                    print(f"Failed to update share {share_id} in database")
                    self.set_status(500)
                    self.write({"error": "Failed to update share"})
                    return
            else:
                db_success = True  # No updates needed

            # Get updated share data for response
            updated_share = _get_share_by_id(DB_CONN, share_id)
            
            # Prepare response
            response_data = {
                "success": True,
                "share_id": share_id,
                "db_persisted": db_success
            }

            if allowed_users is not None:
                response_data["allowed_users"] = updated_share.get('allowed_users')
            if remove_files:
                response_data["removed_files"] = remove_files
                response_data["remaining_files"] = updated_share.get('paths', [])
            if paths is not None:
                response_data["updated_paths"] = updated_share.get('paths', [])

            self.write(response_data)

        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class UserSearchAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Search users by username (for share access control)"""
        if DB_CONN is None:
            self.set_status(500)
            self.write({"error": "Database not available"})
            return
            
        query = self.get_argument('q', '').strip()
        if len(query) < 1:
            self.write({"users": []})
            return
            
        try:
            users = _search_users(DB_CONN, query)
            self.write({"users": users})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class ShareDetailsAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Get share details for a specific file"""
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
            
        file_path = self.get_argument('path', '').strip()
        if not file_path:
            self.set_status(400)
            self.write({"error": "File path is required"})
            return
            
        # Ensure database connection
        global DB_CONN
        if DB_CONN is None:
            self.set_status(500)
            self.write({"error": "Database connection unavailable"})
            return
            
        try:
            # Find shares that contain this file
            matching_shares = _get_shares_for_path(DB_CONN, file_path)

            # Format response
            formatted_shares = []
            for share in matching_shares:
                allowed_users = share.get('allowed_users')
                share_info = {
                    'id': share['id'],
                    'created': share.get('created', ''),
                    'allowed_users': allowed_users if allowed_users is not None else [],
                    'url': f"/shared/{share['id']}",
                    'paths': share.get('paths', [])
                }
                formatted_shares.append(share_info)

            self.write({"shares": formatted_shares})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class ShareDetailsByIdAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Get share details for a specific share ID"""
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return

        share_id = self.get_argument('id', '').strip()
        if not share_id:
            self.set_status(400)
            self.write({"error": "Share ID is required"})
            return

        global DB_CONN
        if DB_CONN is None:
            self.set_status(500)
            self.write({"error": "Database connection unavailable"})
            return

        try:
            share = _get_share_by_id(DB_CONN, share_id)
            if not share:
                self.set_status(404)
                self.write({"error": "Share not found"})
                return

            allowed_users = share.get('allowed_users')
            share_info = {
                'id': share['id'],
                'created': share.get('created', ''),
                'allowed_users': allowed_users if allowed_users is not None else [],
                'url': f"/shared/{share['id']}",
                'paths': share.get('paths', []),
                'secret_token': share.get('secret_token'),
                'share_type': share.get('share_type', 'static'),
                'allow_list': share.get('allow_list', []),
                'avoid_list': share.get('avoid_list', []),
                'expiry_date': share.get('expiry_date')
            }

            self.write({"share": share_info})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class TokenVerificationHandler(tornado.web.RequestHandler):
    def check_xsrf_cookie(self):
        # Disable CSRF protection for token verification
        # This endpoint is meant to be accessed by external users
        pass

class DebugExpiryHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Debug endpoint to test expiry functionality using system time"""
        from datetime import datetime
        
        # Get current system time
        now_system = datetime.now()
        
        # Test with a sample expiry date (system time format)
        test_expiry = "2024-12-31T23:59:59"
        is_expired = _is_share_expired(test_expiry)
        
        # Test with a past date
        past_expiry = "2020-01-01T00:00:00"
        past_expired = _is_share_expired(past_expiry)
        
        self.write({
            "current_system_time": now_system.isoformat(),
            "test_expiry": test_expiry,
            "is_expired": is_expired,
            "past_expiry": past_expiry,
            "past_expired": past_expired,
            "note": "All times are in system timezone (no UTC conversion)"
        })
    
    def get(self, sid):
        """Show token verification page"""
        # Check if share exists
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database connection unavailable")
            return
            
        share = _get_share_by_id(DB_CONN, sid)
        if not share:
            # Debug information
            print(f"DEBUG: Share ID '{sid}' not found in database")
            print(f"DEBUG: DB_CONN is {'None' if DB_CONN is None else 'available'}")
            if DB_CONN:
                try:
                    # Check if shares table exists and has data
                    cursor = DB_CONN.execute("SELECT COUNT(*) FROM shares")
                    count = cursor.fetchone()[0]
                    print(f"DEBUG: Total shares in database: {count}")
                    
                    # List all share IDs for debugging
                    cursor = DB_CONN.execute("SELECT id FROM shares")
                    all_ids = [row[0] for row in cursor.fetchall()]
                    print(f"DEBUG: All share IDs: {all_ids}")
                except Exception as e:
                    print(f"DEBUG: Error checking database: {e}")
            
            self.set_status(404)
            self.write("Invalid share link")
            return
        
        self.render("token_verification.html", share_id=sid)

    def post(self, sid):
        """Verify token and grant access"""
        if DB_CONN is None:
            self.set_status(500)
            self.write({"error": "Database connection unavailable"})
            return
            
        share = _get_share_by_id(DB_CONN, sid)
        if not share:
            self.set_status(404)
            self.write({"error": "Invalid share link"})
            return
        
        try:
            data = json.loads(self.request.body or b'{}')
            provided_token = data.get('token', '').strip()
            stored_token = share.get('secret_token')
            
            if not stored_token:
                # Old share without secret token - allow access
                self.write({"success": True})
                return
                
            if not provided_token:
                self.set_status(400)
                self.write({"error": "Token is required"})
                return
                
            if provided_token != stored_token:
                self.set_status(403)
                self.write({"error": "Invalid token"})
                return
                
            # Token is valid
            self.write({"success": True})
            
        except Exception as e:
            self.set_status(500)
            self.write({"error": "Server error"})

class SharedListHandler(tornado.web.RequestHandler):
    def get(self, sid):
        # Ensure database connection
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database connection unavailable")
            return
            
        share = _get_share_by_id(DB_CONN, sid)
        if not share:
            # Debug information
            print(f"DEBUG: Share ID '{sid}' not found in database")
            print(f"DEBUG: DB_CONN is {'None' if DB_CONN is None else 'available'}")
            if DB_CONN:
                try:
                    # Check if shares table exists and has data
                    cursor = DB_CONN.execute("SELECT COUNT(*) FROM shares")
                    count = cursor.fetchone()[0]
                    print(f"DEBUG: Total shares in database: {count}")
                    
                    # List all share IDs for debugging
                    cursor = DB_CONN.execute("SELECT id FROM shares")
                    all_ids = [row[0] for row in cursor.fetchall()]
                    print(f"DEBUG: All share IDs: {all_ids}")
                except Exception as e:
                    print(f"DEBUG: Error checking database: {e}")
            
            self.set_status(404)
            self.write("Invalid share link")
            return
        
        # Check if share has expired
        expiry_date = share.get('expiry_date')
        if _is_share_expired(expiry_date):
            self.set_status(410)  # Gone
            self.write("This share has expired")
            return
        
        # Check if share requires token verification
        secret_token = share.get('secret_token')
        if secret_token:
            # Check for Authorization header first
            auth_header = self.request.headers.get('Authorization', '')
            provided_token = None
            
            if auth_header.startswith('Bearer '):
                provided_token = auth_header[7:]  # Remove 'Bearer ' prefix
            else:
                # Check for cookie as fallback
                cookie_name = f"share_token_{sid}"
                provided_token = self.get_cookie(cookie_name)
            
            if not provided_token or provided_token != secret_token:
                # No valid token found, redirect to verification
                self.redirect(f"/shared/{sid}/verify")
                return
        # If secret_token is None or empty, no token verification is required
        
        # Check if share has user restrictions
        allowed_users = share.get('allowed_users')
        if allowed_users:
            # Get current user from cookie
            current_user = self.get_secure_cookie("user")
            if not current_user:
                self.set_status(401)
                self.write("Authentication required")
                return
            
            # Decode username if it's bytes
            if isinstance(current_user, bytes):
                current_user = current_user.decode('utf-8')
            
            # Check if current user is in allowed users list
            if current_user not in allowed_users:
                self.set_status(403)
                self.write("Access denied")
                return
        
        import json
        
        # Handle dynamic vs static shares
        share_type = share.get('share_type', 'static')
        allow_list = share.get('allow_list', [])
        avoid_list = share.get('avoid_list', [])
        
        if share_type == 'dynamic':
            # For dynamic shares, scan the folders in real-time
            print(f"DEBUG: Processing dynamic share {sid}")
            dynamic_files = []
            for folder_path in share['paths']:
                try:
                    full_path = os.path.abspath(os.path.join(ROOT_DIR, folder_path))
                    if os.path.isdir(full_path) and is_within_root(full_path, ROOT_DIR):
                        # Recursively scan the folder for current files
                        all_files = _get_all_files_recursive(full_path, folder_path)
                        dynamic_files.extend(all_files)
                        print(f"DEBUG: Found {len(all_files)} files in dynamic folder: {folder_path}")
                except Exception as e:
                    print(f"DEBUG: Error scanning dynamic folder {folder_path}: {e}")
                    continue
            
            # Apply allow/avoid list filtering
            filtered_files = _filter_files_by_patterns(dynamic_files, allow_list, avoid_list)
            print(f"DEBUG: Dynamic share total files: {len(dynamic_files)}, filtered: {len(filtered_files)}")
            self.render("shared_list.html", share_id=sid, files=filtered_files, files_json=json.dumps(filtered_files))
        else:
            # For static shares, use the stored paths
            print(f"DEBUG: Processing static share {sid}")
            print(f"DEBUG: Share paths: {share['paths']}")
            
            # Apply allow/avoid list filtering to static shares
            filtered_files = _filter_files_by_patterns(share['paths'], allow_list, avoid_list)
            print(f"DEBUG: Static share total files: {len(share['paths'])}, filtered: {len(filtered_files)}")
            print(f"DEBUG: JSON encoded paths: {json.dumps(filtered_files)}")
            self.render("shared_list.html", share_id=sid, files=filtered_files, files_json=json.dumps(filtered_files))

class SharedFileHandler(tornado.web.RequestHandler):
    async def get(self, sid, path):
        # Ensure database connection
        if DB_CONN is None:
            self.set_status(500)
            self.write("Database connection unavailable")
            return
            
        share = _get_share_by_id(DB_CONN, sid)
        if not share:
            # Debug information
            print(f"DEBUG: Share ID '{sid}' not found in database")
            print(f"DEBUG: DB_CONN is {'None' if DB_CONN is None else 'available'}")
            if DB_CONN:
                try:
                    # Check if shares table exists and has data
                    cursor = DB_CONN.execute("SELECT COUNT(*) FROM shares")
                    count = cursor.fetchone()[0]
                    print(f"DEBUG: Total shares in database: {count}")
                    
                    # List all share IDs for debugging
                    cursor = DB_CONN.execute("SELECT id FROM shares")
                    all_ids = [row[0] for row in cursor.fetchall()]
                    print(f"DEBUG: All share IDs: {all_ids}")
                except Exception as e:
                    print(f"DEBUG: Error checking database: {e}")
            
            self.set_status(404)
            self.write("Invalid share link")
            return
        
        # Check if share has expired
        expiry_date = share.get('expiry_date')
        if _is_share_expired(expiry_date):
            self.set_status(410)  # Gone
            self.write("This share has expired")
            return
        
        # Check if share requires token verification
        secret_token = share.get('secret_token')
        if secret_token:
            # Check for Authorization header first
            auth_header = self.request.headers.get('Authorization', '')
            provided_token = None
            
            if auth_header.startswith('Bearer '):
                provided_token = auth_header[7:]  # Remove 'Bearer ' prefix
            else:
                # Check for cookie as fallback
                cookie_name = f"share_token_{sid}"
                provided_token = self.get_cookie(cookie_name)
            
            if not provided_token or provided_token != secret_token:
                # No valid token found, redirect to verification
                self.redirect(f"/shared/{sid}/verify")
                return
        # If secret_token is None or empty, no token verification is required
        
        # Check if share has user restrictions
        allowed_users = share.get('allowed_users')
        if allowed_users:
            # Get current user from cookie
            current_user = self.get_secure_cookie("user")
            if not current_user:
                self.set_status(401)
                self.write("Authentication required")
                return
            
            # Decode username if it's bytes
            if isinstance(current_user, bytes):
                current_user = current_user.decode('utf-8')
            
            # Check if current user is in allowed users list
            if current_user not in allowed_users:
                self.set_status(403)
                self.write("Access denied")
                return
        
        # Check if file is accessible based on share type
        share_type = share.get('share_type', 'static')
        allow_list = share.get('allow_list', [])
        avoid_list = share.get('avoid_list', [])
        
        if share_type == 'dynamic':
            # For dynamic shares, check if file is within any of the shared folders
            file_accessible = False
            for folder_path in share['paths']:
                try:
                    full_folder_path = os.path.abspath(os.path.join(ROOT_DIR, folder_path))
                    if os.path.isdir(full_folder_path) and is_within_root(full_folder_path, ROOT_DIR):
                        # Check if the requested file is within this folder
                        full_file_path = os.path.abspath(os.path.join(ROOT_DIR, path))
                        if full_file_path.startswith(full_folder_path) and os.path.isfile(full_file_path):
                            file_accessible = True
                            break
                except Exception as e:
                    print(f"DEBUG: Error checking dynamic folder {folder_path}: {e}")
                    continue
            
            if not file_accessible:
                self.set_status(403)
                self.write("File not in share")
                return
        else:
            # For static shares, check if file is in the stored paths
            if path not in share['paths']:
                self.set_status(403)
                self.write("File not in share")
                return
        
        # Apply allow/avoid list filtering
        if allow_list or avoid_list:
            if not _filter_files_by_patterns([path], allow_list, avoid_list):
                self.set_status(403)
                self.write("File not allowed by filter rules")
                return
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not (is_within_root(abspath, ROOT_DIR) and os.path.isfile(abspath)):
            self.set_status(404)
            self.write("File not found")
            return
        self.set_header('Content-Type', 'text/plain; charset=utf-8')
        # Stream in chunks using async I/O to avoid blocking event loop
        try:
            # Use aiofiles for async binary reading
            async with aiofiles.open(abspath, 'rb') as f:
                while True:
                    chunk = await f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    # Decode chunk safely and write
                    self.write(chunk.decode('utf-8', errors='replace'))
                    await self.flush()
        except Exception:
            # As a last resort, send minimal error
            self.set_status(500)
            self.write("Error streaming file")
            return


class SuperSearchHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Render the super search page"""
        # Check if super search is enabled
        if not is_feature_enabled("super_search"):
            self.set_status(403)
            self.write("Super search is disabled.")
            return
            
        # Get the current path from query parameter
        current_path = self.get_argument("path", "").strip()
        # Ensure path is safe and within ROOT_DIR
        if current_path:
            current_path = current_path.strip('/')
        self.render("super_search.html", current_path=current_path)


class SuperSearchWebSocketHandler(tornado.websocket.WebSocketHandler):
    """WebSocket handler for streaming super search results"""
    
    # Use connection manager with configurable limits for search operations
    connection_manager = WebSocketConnectionManager("search", default_max_connections=100, default_idle_timeout=180)
    
    def get_current_user(self) -> str | None:
        return self.get_secure_cookie("user")

    def check_origin(self, origin):
        # Improved origin validation (Priority 2)
        return is_valid_websocket_origin(self, origin)

    def open(self):
        if not self.current_user:
            self.close()
            return
            
        # Check if super search is enabled
        if not is_feature_enabled("super_search"):
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Super search is disabled.'
            }))
            self.close(code=1011, reason="Super search disabled")
            return
            
        if not self.connection_manager.add_connection(self):
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Connection limit exceeded. Please try again later.'
            }))
            self.close(code=1013, reason="Connection limit exceeded")
            return
            
        self.search_cancelled = False

    def write_message(self, message):
        """Override write_message to track activity"""
        result = super().write_message(message)
        self.connection_manager.update_activity(self)
        return result

    async def on_message(self, message):
        """Handle search request from client"""
        if self.search_cancelled:
            return
            
        # Double-check that super search is still enabled
        if not is_feature_enabled("super_search"):
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Super search has been disabled.'
            }))
            return
            
        try:
            data = json.loads(message)
            pattern = data.get('pattern', '').strip()
            search_text = data.get('search_text', '').strip()
            search_mode = data.get('search_mode', 'content').strip()
            
            if not pattern or not search_text:
                self.write_message(json.dumps({
                    'type': 'error',
                    'message': 'Both pattern and search text are required'
                }))
                return
                
            # Start the search
            await self.perform_search(pattern, search_text, search_mode)
            
        except json.JSONDecodeError:
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Invalid JSON format'
            }))
        except Exception as e:
            self.write_message(json.dumps({
                'type': 'error',
                'message': f'Search error: {str(e)}'
            }))

    async def perform_search(self, pattern, search_text, search_mode='content'):
        """Perform the super search and stream results"""
        try:
            # Send search start notification
            self.write_message(json.dumps({
                'type': 'search_start',
                'pattern': pattern,
                'search_text': search_text
            }))
            
            # Find matching files using glob pattern
            matching_files = []
            try:
                # Normalize pattern to use platform-specific separators
                normalized_pattern = pattern.replace('/', os.sep).replace('\\', os.sep)
                
                # Always search from ROOT_DIR - ensure pattern is relative to root
                if os.path.isabs(normalized_pattern):
                    # If absolute path provided, make it relative to ROOT_DIR
                    try:
                        normalized_pattern = os.path.relpath(normalized_pattern, ROOT_DIR)
                    except ValueError:
                        # If can't make relative (different drives on Windows), reject
                        self.write_message(json.dumps({
                            'type': 'error',
                            'message': 'Pattern must be within the server root directory'
                        }))
                        return
                
                # Strip leading separators to ensure relative path
                normalized_pattern = normalized_pattern.lstrip(os.sep)
                
                # Construct search pattern relative to ROOT_DIR
                search_pattern = os.path.join(ROOT_DIR, normalized_pattern)
                
                # Use pathlib for better cross-platform support
                root_path = pathlib.Path(ROOT_DIR)
                
                # Use glob to find matching files
                for file_path in glob.glob(search_pattern, recursive=True):
                    if self.search_cancelled:
                        return
                    
                    # Ensure file is within ROOT_DIR and is actually a file
                    abs_path = os.path.abspath(file_path)
                    path_obj = pathlib.Path(abs_path)
                    
                    # Security check: ensure the resolved path is within ROOT_DIR
                    try:
                        path_obj.relative_to(root_path)
                    except ValueError:
                        # Path is outside ROOT_DIR, skip it
                        continue
                    
                    if path_obj.is_file():
                        # Convert back to relative path for display using platform separators
                        rel_path = os.path.relpath(abs_path, ROOT_DIR)
                        matching_files.append((rel_path, abs_path))
                        
            except Exception as e:
                self.write_message(json.dumps({
                    'type': 'error',
                    'message': f'Pattern matching error: {str(e)}'
                }))
                return
            
            if not matching_files:
                self.write_message(json.dumps({
                    'type': 'no_files',
                    'message': f'No files found matching pattern: {pattern}'
                }))
                return
            
            # Search within each matching file
            total_files = len(matching_files)
            processed_files = 0
            
            for rel_path, abs_path in matching_files:
                if self.search_cancelled:
                    return
                
                processed_files += 1
                
                # Send file start notification
                self.write_message(json.dumps({
                    'type': 'file_start',
                    'file_path': rel_path,
                    'progress': {'current': processed_files, 'total': total_files}
                }))
                
                # Search based on mode
                try:
                    if search_mode == 'filename':
                        await self.search_filename(rel_path, abs_path, search_text)
                    else:
                        await self.search_in_file(rel_path, abs_path, search_text)
                except Exception as e:
                    self.write_message(json.dumps({
                        'type': 'file_error',
                        'file_path': rel_path,
                        'message': f'Error searching in file: {str(e)}'
                    }))
                
                # Send file end notification
                self.write_message(json.dumps({
                    'type': 'file_end',
                    'file_path': rel_path
                }))
                
                # Allow other coroutines to run
                await asyncio.sleep(0)
            
            # Send search completion
            self.write_message(json.dumps({
                'type': 'search_complete',
                'files_processed': processed_files
            }))
            
        except Exception as e:
            self.write_message(json.dumps({
                'type': 'error',
                'message': f'Search failed: {str(e)}'
            }))

    async def search_in_file(self, rel_path, abs_path, search_text):
        """Search for text within a single file and stream matches"""
        try:
            file_size = os.path.getsize(abs_path)
            
            # Use efficient search method based on file size
            if MMapFileHandler.should_use_mmap(file_size):
                await self.search_with_mmap(rel_path, abs_path, search_text)
            else:
                await self.search_traditional(rel_path, abs_path, search_text)
                
        except Exception as e:
            self.write_message(json.dumps({
                'type': 'file_error',
                'file_path': rel_path,
                'message': f'Cannot read file: {str(e)}'
            }))

    async def search_with_mmap(self, rel_path, abs_path, search_text):
        """Search using memory mapping for large files"""
        try:
            # Use thread pool for mmap operations to avoid blocking
            import concurrent.futures
            
            def search_mmap():
                matches = []
                filter_expression = FilterExpression(search_text)
                with open(abs_path, 'rb') as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        current_pos = 0
                        line_number = 1
                        
                        while current_pos < len(mm):
                            if self.search_cancelled:
                                return matches
                            
                            newline_pos = mm.find(b'\n', current_pos)
                            if newline_pos == -1:
                                # Last line
                                line_bytes = mm[current_pos:]
                                line_content = line_bytes.decode('utf-8', errors='replace')
                                if filter_expression.matches(line_content):
                                    matches.append((line_number, line_content))
                                break
                            
                            line_bytes = mm[current_pos:newline_pos]
                            line_content = line_bytes.decode('utf-8', errors='replace')
                            if filter_expression.matches(line_content):
                                matches.append((line_number, line_content))
                            
                            current_pos = newline_pos + 1
                            line_number += 1
                return matches
            
            # Run mmap search in thread pool
            with concurrent.futures.ThreadPoolExecutor() as executor:
                matches = await asyncio.get_event_loop().run_in_executor(executor, search_mmap)
            
            # Send matches asynchronously
            for line_number, line_content in matches:
                if self.search_cancelled:
                    break
                self.send_match(rel_path, line_number, line_content, search_text)
                # Yield control between sends
                if line_number % 100 == 0:
                    await asyncio.sleep(0)
                            
        except (OSError, ValueError):
            # Fallback to traditional search
            await self.search_traditional(rel_path, abs_path, search_text)

    async def search_traditional(self, rel_path, abs_path, search_text):
        """Search using async file reading for small files"""
        try:
            # Use aiofiles for non-blocking file reading with complex filtering
            filter_expression = FilterExpression(search_text)
            async with aiofiles.open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                line_number = 1
                async for line in f:
                    if self.search_cancelled:
                        return
                    
                    if filter_expression.matches(line):
                        self.send_match(rel_path, line_number, line.rstrip('\n'), search_text)
                    
                    line_number += 1
                    
                    # Yield control periodically to prevent blocking
                    if line_number % 1000 == 0:
                        await asyncio.sleep(0)
                        
        except Exception as e:
            self.write_message(json.dumps({
                'type': 'file_error',
                'file_path': rel_path,
                'message': f'Error reading file: {str(e)}'
            }))

    def send_match(self, file_path, line_number, line_content, search_text):
        """Send a match result to the client"""
        # Find all match positions in the line
        match_positions = []
        start_pos = 0
        while True:
            pos = line_content.find(search_text, start_pos)
            if pos == -1:
                break
            match_positions.append(pos)
            start_pos = pos + 1
        
        self.write_message(json.dumps({
            'type': 'match',
            'file_path': file_path,
            'line_number': line_number,
            'line_content': line_content,
            'search_text': search_text,
            'match_positions': match_positions
        }))

    async def search_filename(self, rel_path, abs_path, search_text):
        """Search for text in filename"""
        try:
            # Get just the filename from the path
            filename = os.path.basename(rel_path)
            
            # Check if search text is in filename (case-sensitive)
            if search_text in filename:
                # For filename search, we show the full path as the "line content"
                # and use line number 0 to indicate it's a filename match
                self.send_match(rel_path, 0, f"📁 {rel_path}", search_text)
                
        except Exception as e:
            self.write_message(json.dumps({
                'type': 'file_error',
                'file_path': rel_path,
                'message': f'Error searching filename: {str(e)}'
            }))

    def on_close(self):
        self.search_cancelled = True
        self.connection_manager.remove_connection(self)


def make_app(settings, ldap_enabled=False, ldap_server=None, ldap_base_dn=None, ldap_user_template=None, ldap_filter_template=None, ldap_attributes=None, ldap_attribute_map=None, admin_users=None):
    settings["template_path"] = os.path.join(os.path.dirname(__file__), "templates")
    # Limit request size to avoid Tornado rejecting large uploads with
    # "Content-Length too long" before our handler can respond.
    settings.setdefault("max_body_size", MAX_FILE_SIZE)
    settings.setdefault("max_buffer_size", MAX_FILE_SIZE)
    
    if ldap_enabled:
        settings["ldap_server"] = ldap_server
        settings["ldap_base_dn"] = ldap_base_dn
        settings["ldap_user_template"] = ldap_user_template
        settings["ldap_filter_template"] = ldap_filter_template
        settings["ldap_attributes"] = ldap_attributes
        settings["ldap_attribute_map"] = ldap_attribute_map
    
    # Add admin users configuration to settings
    if admin_users:
        settings["admin_users"] = admin_users
    
    if ldap_enabled:
        login_handler = LDAPLoginHandler
    else:
        login_handler = LoginHandler

    # Build routes list
    routes = [
        (r"/", RootHandler),
        (r"/login", login_handler),
        (r"/logout", LogoutHandler),
        (r"/profile", ProfileHandler),
        (r"/admin/login", AdminLoginHandler),
        (r"/admin", AdminHandler),
        (r"/admin/users", AdminUsersHandler),
        (r"/admin/users/create", UserCreateHandler),
        (r"/admin/users/edit/([0-9]+)", UserEditHandler),
        (r"/admin/users/delete", UserDeleteHandler),
        (r"/admin/websocket-stats", WebSocketStatsHandler),
        (r"/stream/(.*)", FileStreamHandler),
        (r"/features", FeatureFlagSocketHandler),
        (r"/upload", UploadHandler),
        (r"/delete", DeleteHandler),
        (r"/rename", RenameHandler),
        (r"/edit/(.*)", EditViewHandler),
        (r"/edit", EditHandler),
        (r"/api/files/(.*)", FileListAPIHandler),
        (r"/api/users/search", UserSearchAPIHandler),
        (r"/api/share/details", ShareDetailsAPIHandler),
        (r"/api/share/details_by_id", ShareDetailsByIdAPIHandler),
        (r"/share", ShareFilesHandler),
        (r"/share/create", ShareCreateHandler),
        (r"/share/revoke", ShareRevokeHandler),
        (r"/share/list", ShareListAPIHandler),
        (r"/share/update", ShareUpdateHandler),
        (r"/shared/([A-Za-z0-9_\-]+)/verify", TokenVerificationHandler),
        (r"/debug/expiry", DebugExpiryHandler),
        (r"/shared/([A-Za-z0-9_\-]+)", SharedListHandler),
        (r"/shared/([A-Za-z0-9_\-]+)/file/(.*)", SharedFileHandler),
        (r"/search", SuperSearchHandler),
        (r"/search/ws", SuperSearchWebSocketHandler),
        (r"/files/(.*)", MainHandler),
    ]
    
    # Add LDAP routes only if LDAP is enabled
    if ldap_enabled:
        routes.extend([
            (r"/admin/ldap", LDAPConfigHandler),
            (r"/admin/ldap/create", LDAPConfigCreateHandler),
            (r"/admin/ldap/edit/([0-9]+)", LDAPConfigEditHandler),
            (r"/admin/ldap/delete", LDAPConfigDeleteHandler),
            (r"/admin/ldap/sync", LDAPSyncHandler),
        ])
    
    return tornado.web.Application(routes, **settings)


def print_banner():
    """Print simple ASCII art banner for aird"""
    banner = """
 █████╗ ██╗██████╗ ██████╗ 
██╔══██╗██║██╔══██╗██╔══██╗
███████║██║██████╔╝██║  ██║
██╔══██║██║██╔══██╗██║  ██║
██║  ██║██║██║  ██║██████╔╝
╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚═════╝ 
"""
    print(banner)

def main():
    print_banner()
    
    parser = argparse.ArgumentParser(description="Run Aird")
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument("--root", help="Root directory to serve")
    parser.add_argument("--port", type=int, help="Port to listen on")
    parser.add_argument("--token", help="Access token for login")
    parser.add_argument("--admin-token", help="Access token for admin login")
    parser.add_argument("--ldap", action="store_true", help="Enable LDAP authentication")
    parser.add_argument("--ldap-server", help="LDAP server address")
    parser.add_argument("--ldap-base-dn", help="LDAP base DN for user search")
    parser.add_argument("--ldap-user-template", help="LDAP user template (default: uid={username},{ldap_base_dn})")
    parser.add_argument("--ldap-filter-template", help="LDAP filter template for user search")
    parser.add_argument("--ldap-attributes", help="LDAP attributes to retrieve (comma-separated)")
    parser.add_argument("--hostname", help="Host name for the server")
    parser.add_argument("--ssl-cert", help="Path to SSL certificate file")
    parser.add_argument("--ssl-key", help="Path to SSL private key file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

    config = {}
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    root = args.root or config.get("root") or os.getcwd()
    port = args.port or config.get("port") or 8000
    # Determine if tokens were explicitly provided; if not, we'll print the generated values
    token_provided_explicitly = bool(args.token or config.get("token") or os.environ.get("AIRD_ACCESS_TOKEN"))
    admin_token_provided_explicitly = bool(args.admin_token or config.get("admin_token"))

    token = args.token or config.get("token") or os.environ.get("AIRD_ACCESS_TOKEN") or secrets.token_urlsafe(32)
    admin_token = args.admin_token or config.get("admin_token") or secrets.token_urlsafe(32)


    ldap_enabled = args.ldap or config.get("ldap", False)
    ldap_server = args.ldap_server or config.get("ldap_server")
    ldap_base_dn = args.ldap_base_dn or config.get("ldap_base_dn")
    ldap_user_template = args.ldap_user_template or config.get("ldap_user_template", "uid={username},{ldap_base_dn}")
    ldap_filter_template = args.ldap_filter_template or config.get("ldap_filter_template")
    ldap_attributes = args.ldap_attributes or config.get("ldap_attributes", ["cn", "mail", "memberOf"])
    ldap_attribute_map = config.get("ldap_attribute_map", [])
    
    # Parse comma-separated attributes if provided as string
    if isinstance(ldap_attributes, str):
        ldap_attributes = [attr.strip() for attr in ldap_attributes.split(",")]
    
    # SSL configuration
    ssl_cert = args.ssl_cert or config.get("ssl_cert")
    ssl_key = args.ssl_key or config.get("ssl_key")
    
    # Admin users configuration
    admin_users = config.get("admin_users", [])
    
    host_name = args.hostname or config.get("hostname") or socket.getfqdn()

    if ldap_enabled:
        if not ldap_server:
            print("Error: LDAP is enabled, but --ldap-server is not configured.")
            return
        if not ldap_base_dn:
            print("Error: LDAP is enabled, but --ldap-base-dn is not configured.")
            return
        if not ldap_user_template:
            print("Error: LDAP is enabled, but --ldap-user-template is not configured.")
            return
        if not ldap_filter_template:
            print("Error: LDAP is enabled, but --ldap-filter-template is not configured.")
            return
        if not ldap_attributes:
            print("Error: LDAP is enabled, but --ldap-attributes is not configured.")
            return

    # SSL validation
    if ssl_cert and not ssl_key:
        print("Error: SSL certificate provided but SSL key is missing. Both --ssl-cert and --ssl-key are required for SSL.")
        return
    if ssl_key and not ssl_cert:
        print("Error: SSL key provided but SSL certificate is missing. Both --ssl-cert and --ssl-key are required for SSL.")
        return
    if ssl_cert and ssl_key:
        # Validate that certificate and key files exist
        if not os.path.exists(ssl_cert):
            print(f"Error: SSL certificate file not found: {ssl_cert}")
            return
        if not os.path.exists(ssl_key):
            print(f"Error: SSL key file not found: {ssl_key}")
            return

    global ACCESS_TOKEN, ADMIN_TOKEN, ROOT_DIR, DB_CONN, DB_PATH
    ACCESS_TOKEN = token
    ADMIN_TOKEN = admin_token
    ROOT_DIR = os.path.abspath(root)

    # Generate separate cookie secret for better security
    cookie_secret = secrets.token_urlsafe(64)
    
    settings = {
        "cookie_secret": cookie_secret,
        "xsrf_cookies": True,  # Enable CSRF protection
        "login_url": "/login",
        "admin_login_url": "/admin/login",
    }

    # Initialize SQLite persistence under OS data dir
    try:
        data_dir = _get_data_dir()
        DB_PATH = os.path.join(data_dir, 'aird.sqlite3')
        db_exists = os.path.exists(DB_PATH)
        print(f"SQLite database path: {DB_PATH}")
        print(f"Database already exists: {'Yes' if db_exists else 'No (will be created)'}")
        DB_CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
        _init_db(DB_CONN)
        # Load persisted feature flags and merge
        persisted_flags = _load_feature_flags(DB_CONN)
        if persisted_flags:
            for k, v in persisted_flags.items():
                FEATURE_FLAGS[k] = bool(v)
        
        # Start LDAP sync scheduler
        _start_ldap_sync_scheduler(DB_CONN)
        # Database-only persistence for shares
        print(f"Shares are now persisted directly in database")
        
        # Assign admin privileges to configured admin users
        _assign_admin_privileges(DB_CONN, admin_users)

        # Ensure database connection is working
        if DB_CONN is None:
            print("WARNING: Database connection is None, attempting to create...")
            try:
                DB_PATH = os.path.join(os.path.expanduser('~'), '.local', 'aird', 'aird.sqlite3')
                os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
                DB_CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
                _init_db(DB_CONN)
                print(f"Created emergency database connection: {DB_CONN}")
            except Exception as db_error:
                print(f"Failed to create emergency database connection: {db_error}")

    except Exception as e:
        print(f"Database initialization failed: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        DB_CONN = None
        print(f"DB_CONN set to None: {DB_CONN}")

    # Print tokens when they were not explicitly provided, so users can log in
    if not token_provided_explicitly:
        print(f"Access token (generated): {token}")
    if not admin_token_provided_explicitly:
        print(f"Admin token (generated): {admin_token}")
    app = make_app(settings, ldap_enabled, ldap_server, ldap_base_dn, ldap_user_template, ldap_filter_template, ldap_attributes, ldap_attribute_map, admin_users)
    
    # Configure SSL if certificates are provided
    ssl_options = None
    if ssl_cert and ssl_key:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(ssl_cert, ssl_key)
        ssl_options = ssl_context
    
    while True:
        try:
            if ssl_options:
                app.listen(
                    port,
                    ssl_options=ssl_options,
                    max_body_size=MAX_FILE_SIZE,
                    max_buffer_size=MAX_FILE_SIZE,
                )
                print(f"Serving HTTPS on 0.0.0.0 port {port} (https://0.0.0.0:{port}/) ...")
                print(f"https://{host_name}:{port}/")
            else:
                app.listen(
                    port,
                    max_body_size=MAX_FILE_SIZE,
                    max_buffer_size=MAX_FILE_SIZE,
                )
                print(f"Serving HTTP on 0.0.0.0 port {port} (http://0.0.0.0:{port}/) ...")
                print(f"http://{host_name}:{port}/")

            # Setup periodic cleanup of expired shares
            def cleanup_expired_shares_periodic():
                """Periodic task to cleanup expired shares"""
                global DB_CONN
                if DB_CONN:
                    deleted = _cleanup_expired_shares(DB_CONN)
                    if deleted > 0:
                        logging.info(f"Cleaned up {deleted} expired share(s)")
                # Schedule next cleanup in 1 hour (3600 seconds)
                tornado.ioloop.IOLoop.current().call_later(3600, cleanup_expired_shares_periodic)
            
            # Start cleanup in 1 hour
            tornado.ioloop.IOLoop.current().call_later(3600, cleanup_expired_shares_periodic)
            
            tornado.ioloop.IOLoop.current().start()
            break
        except OSError:
            port += 1
    
if __name__ == "__main__":
    main()
