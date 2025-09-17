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

SHARES = {}

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
            paths TEXT NOT NULL
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
        rows = conn.execute("SELECT id, created, paths FROM shares").fetchall()
        for sid, created, paths_json in rows:
            try:
                paths = json.loads(paths_json) if paths_json else []
            except Exception:
                paths = []
            loaded[sid] = {"paths": paths, "created": created}
    except Exception:
        return {}
    return loaded

def _insert_share(conn: sqlite3.Connection, sid: str, created: str, paths: list[str]) -> None:
    try:
        with conn:
            conn.execute(
                "REPLACE INTO shares (id, created, paths) VALUES (?, ?, ?)",
                (sid, created, json.dumps(paths)),
            )
    except Exception:
        pass

def _delete_share(conn: sqlite3.Connection, sid: str) -> None:
    try:
        with conn:
            conn.execute("DELETE FROM shares WHERE id = ?", (sid,))
    except Exception:
        pass

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
            server = Server(self.settings['ldap_server'], get_info=ALL)
            conn = Connection(server, user=f"uid={username},{self.settings['ldap_base_dn']}", password=password, auto_bind=True)
            if conn.bind():
                self.set_secure_cookie("user", username, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                self.redirect("/files/")
            else:
                self.render("login.html", error="Invalid username or password.", settings=self.settings)
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
        
        self.render("admin.html",
                   features=current_features,
                   websocket_config=current_websocket_config,
                   rust_available=RUST_AVAILABLE,
                   rust_stats=rust_stats)

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

class ProfileHandler(BaseHandler):
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
                       success=None)
            return
            
        try:
            user = _get_user_by_username(DB_CONN, current_user)
            if not user:
                self.set_status(404)
                self.write("User not found")
                return
                
            self.render("profile.html", user=user, error=None, success=None)
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
                       success=None)
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
                self.render("profile.html", user=user, error="Current password is required.", success=None)
                return
                
            if not _verify_password(current_password, user['password_hash']):
                self.render("profile.html", user=user, error="Current password is incorrect.", success=None)
                return
            
            if action == "change_password":
                new_password = self.get_argument("new_password", "").strip()
                confirm_password = self.get_argument("confirm_password", "").strip()
                
                # Input validation
                if not new_password:
                    self.render("profile.html", user=user, error="New password is required.", success=None)
                    return
                    
                if len(new_password) < 6:
                    self.render("profile.html", user=user, error="Password must be at least 6 characters.", success=None)
                    return
                    
                if new_password != confirm_password:
                    self.render("profile.html", user=user, error="New passwords do not match.", success=None)
                    return
                
                # Update password
                if _update_user(DB_CONN, user['id'], password=new_password):
                    self.render("profile.html", user=user, error=None, success="Password updated successfully!")
                else:
                    self.render("profile.html", user=user, error="Failed to update password.", success=None)
            else:
                self.render("profile.html", user=user, error="Invalid action.", success=None)
                
        except Exception as e:
            logging.getLogger(__name__).exception(f"Error updating profile for user {current_user}: {str(e)}")
            self.render("profile.html", user=None, error=f"Error updating profile: {str(e)}", success=None)

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
            # Collect all shared paths for efficient lookup
            all_shared_paths = set()
            for share in SHARES.values():
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
                
            result = {
                "path": path,
                "files": [
                    {
                        "name": f["name"],
                        "is_dir": f["is_dir"],
                        "size_str": f.get("size_str", "-"),
                        "modified": f.get("modified", "-")
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
        self.render("share.html", shares=SHARES)

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
            valid_paths = []
            for p in paths:
                ap = os.path.abspath(os.path.join(ROOT_DIR, p))
                if is_within_root(ap, ROOT_DIR) and os.path.isfile(ap):
                    valid_paths.append(p)
            if not valid_paths:
                self.set_status(400)
                self.write({"error": "No valid files"})
                return
sid = secrets.token_urlsafe(24)  # Increase entropy to reduce guessing risk (Priority 2)
            created = datetime.utcnow().isoformat()
            SHARES[sid] = {"paths": valid_paths, "created": created}
            # Persist to DB
            try:
                if DB_CONN is not None:
                    _insert_share(DB_CONN, sid, created, valid_paths)
            except Exception:
                pass
            self.write({"id": sid, "url": f"/shared/{sid}"})
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
        if sid in SHARES:
            del SHARES[sid]
            # Persist deletion
            try:
                if DB_CONN is not None:
                    _delete_share(DB_CONN, sid)
            except Exception:
                pass
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
        self.write({"shares": SHARES})

class SharedListHandler(tornado.web.RequestHandler):
    def get(self, sid):
        share = SHARES.get(sid)
        if not share:
            self.set_status(404)
            self.write("Invalid share link")
            return
        self.render("shared_list.html", share_id=sid, files=share['paths'])

class SharedFileHandler(tornado.web.RequestHandler):
    async def get(self, sid, path):
        share = SHARES.get(sid)
        if not share:
            self.set_status(404)
            self.write("Invalid share link")
            return
        if path not in share['paths']:
            self.set_status(403)
            self.write("File not in share")
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
            
            if not pattern or not search_text:
                self.write_message(json.dumps({
                    'type': 'error',
                    'message': 'Both pattern and search text are required'
                }))
                return
                
            # Start the search
            await self.perform_search(pattern, search_text)
            
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

    async def perform_search(self, pattern, search_text):
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
                
                # Search within the file
                try:
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

    def on_close(self):
        self.search_cancelled = True
        self.connection_manager.remove_connection(self)


def make_app(settings, ldap_enabled=False, ldap_server=None, ldap_base_dn=None):
    settings["template_path"] = os.path.join(os.path.dirname(__file__), "templates")
    # Limit request size to avoid Tornado rejecting large uploads with
    # "Content-Length too long" before our handler can respond.
    settings.setdefault("max_body_size", MAX_FILE_SIZE)
    settings.setdefault("max_buffer_size", MAX_FILE_SIZE)
    
    if ldap_enabled:
        settings["ldap_server"] = ldap_server
        settings["ldap_base_dn"] = ldap_base_dn
        login_handler = LDAPLoginHandler
    else:
        login_handler = LoginHandler

    return tornado.web.Application([
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
        (r"/share", ShareFilesHandler),
        (r"/share/create", ShareCreateHandler),
        (r"/share/revoke", ShareRevokeHandler),
        (r"/share/list", ShareListAPIHandler),
        (r"/shared/([A-Za-z0-9_\-]+)", SharedListHandler),
        (r"/shared/([A-Za-z0-9_\-]+)/file/(.*)", SharedFileHandler),
        (r"/search", SuperSearchHandler),
        (r"/search/ws", SuperSearchWebSocketHandler),
        (r"/files/(.*)", MainHandler),
    ], **settings)


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
    parser.add_argument("--hostname", help="Host name for the server")
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
    host_name = args.hostname or config.get("hostname") or socket.getfqdn()

    if ldap_enabled and not (ldap_server and ldap_base_dn):
        print("Error: LDAP is enabled, but --ldap-server and --ldap-base-dn are not configured.")
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
        # Load persisted shares
        persisted_shares = _load_shares(DB_CONN)
        if persisted_shares:
            SHARES.update(persisted_shares)
    except Exception:
        DB_CONN = None

    # Print tokens when they were not explicitly provided, so users can log in
    if not token_provided_explicitly:
        print(f"Access token (generated): {token}")
    if not admin_token_provided_explicitly:
        print(f"Admin token (generated): {admin_token}")
    app = make_app(settings, ldap_enabled, ldap_server, ldap_base_dn)
    while True:
        try:
            app.listen(
                port,
                max_body_size=MAX_FILE_SIZE,
                max_buffer_size=MAX_FILE_SIZE,
            )
            print(f"Serving HTTP on 0.0.0.0 port {port} (http://0.0.0.0:{port}/) ...")
            print(f"http://{host_name}:{port}/")
            tornado.ioloop.IOLoop.current().start()
            break
        except OSError:
            port += 1
    
if __name__ == "__main__":
    main()
