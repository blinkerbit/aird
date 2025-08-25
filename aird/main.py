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

import tornado.ioloop
import tornado.web
import socket
import tornado.websocket
import shutil
from collections import deque
from ldap3 import Server, Connection, ALL
from datetime import datetime
import gzip
import mimetypes
from io import BytesIO
import tempfile
from urllib.parse import unquote
import aiofiles

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
}


# Maximum upload size: 10 GB
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024
MAX_READABLE_FILE_SIZE = 10 * 1024 * 1024
CHUNK_SIZE = 1024 * 64
# Minimum file size to use mmap (avoid overhead for small files)
MMAP_MIN_SIZE = 1024 * 1024  # 1MB

SHARES = {}

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
    if ext in [".txt", ".md"]:
        return "📄"
    elif ext in [".jpg", ".jpeg", ".png", ".gif"]:
        return "🖼️"
    elif ext in [".py", ".js", ".java", ".cpp"]:
        return "💻"
    elif ext in [".zip", ".rar"]:
        return "🗜️"
    else:
        return "📦"


class FeatureFlagSocketHandler(tornado.websocket.WebSocketHandler):
    connections: Set['FeatureFlagSocketHandler'] = set()

    def open(self):
        FeatureFlagSocketHandler.connections.add(self)
        # Load current feature flags from SQLite and send to client
        current_flags = self._get_current_feature_flags()
        self.write_message(json.dumps(current_flags))

    def on_close(self):
        FeatureFlagSocketHandler.connections.remove(self)

    def check_origin(self, origin):
        # Only allow connections from the same host
        allowed_origins = [
            f"http://{self.request.host}",
            f"https://{self.request.host}",
            "http://localhost:8000",
            "http://127.0.0.1:8000"
        ]
        return origin in allowed_origins

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
        if not cls.connections:
            return

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

        # Send to all connected clients
        for connection in cls.connections:
            try:
                connection.write_message(json.dumps(current_flags))
            except Exception:
                # Remove dead connections
                cls.connections.discard(connection)


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
                self.set_secure_cookie("user", username)
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
        token = self.get_argument("token", "").strip()
        next_url = self.get_argument("next", "/files/")
        
        # Input validation
        if not token:
            self.render("login.html", error="Token is required.", settings=self.settings, next_url=next_url)
            return
            
        if len(token) > 512:  # Reasonable token length limit
            self.render("login.html", error="Invalid token.", settings=self.settings, next_url=next_url)
            return
            
        if token == ACCESS_TOKEN:
            self.set_secure_cookie("user", "authenticated")
            self.redirect(next_url)
        else:
            self.render("login.html", error="Invalid token. Try again.", settings=self.settings, next_url=next_url)

class AdminLoginHandler(BaseHandler):
    def get(self):
        if self.get_current_admin():
            self.redirect("/admin")
            return
        self.render("admin_login.html", error=None)

    def post(self):
        token = self.get_argument("token", "").strip()
        
        # Input validation
        if not token:
            self.render("admin_login.html", error="Token is required.")
            return
            
        if len(token) > 512:  # Reasonable token length limit
            self.render("admin_login.html", error="Invalid token.")
            return
            
        if token == ADMIN_TOKEN:
            self.set_secure_cookie("admin", "authenticated")
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
        if not self.get_current_admin():
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

        self.render("admin.html",
                   features=current_features,
                   rust_available=RUST_AVAILABLE,
                   rust_stats=rust_stats)

    @tornado.web.authenticated
    def post(self):
        FEATURE_FLAGS["compression"] = self.get_argument("compression", "off") == "on"
        if not self.get_current_admin():
            self.set_status(403)
            self.write("Forbidden")
            return
        
        FEATURE_FLAGS["file_upload"] = self.get_argument("file_upload", "off") == "on"
        FEATURE_FLAGS["file_delete"] = self.get_argument("file_delete", "off") == "on"
        FEATURE_FLAGS["file_rename"] = self.get_argument("file_rename", "off") == "on"
        FEATURE_FLAGS["file_download"] = self.get_argument("file_download", "off") == "on"
        FEATURE_FLAGS["file_edit"] = self.get_argument("file_edit", "off") == "on"
        FEATURE_FLAGS["file_share"] = self.get_argument("file_share", "off") == "on"
        
        # Persist feature flags
        try:
            if DB_CONN is not None:
                _save_feature_flags(DB_CONN, FEATURE_FLAGS)
        except Exception:
            pass

        FeatureFlagSocketHandler.send_updates()
        self.redirect("/admin")

def get_relative_path(path, root):
    if path.startswith(root):
        return os.path.relpath(path, root)
    return path

class MainHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, path):
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))

        if not abspath.startswith(ROOT_DIR):
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
                                with open(abspath, 'rb') as f:
                                    file_data = f.read()
                                compressed_data = HybridCompressionHandler.compress_data(file_data, level=6)
                                self.write(compressed_data)
                                await self.flush()
                                return
                            except Exception as e:
                                logger.warning(f"Rust compression failed, using Python fallback: {e}")
                        
                        # Fallback to Python gzip compression
                        buffer = BytesIO()
                        with open(abspath, 'rb') as f_in, gzip.GzipFile(fileobj=buffer, mode='wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)

                        self.write(buffer.getvalue())
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
                self.set_header('Content-Type', 'text/plain; charset=utf-8')
                self.write(f"Streaming file: {filename}\n\n")
                await self.flush()
                # Stream line-by-line as soon as it's read
                with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                    for raw in f:
                        # Avoid double spacing: strip one trailing newline and let browser render line breaks
                        line = raw[:-1] if raw.endswith('\n') else raw
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

            try:
                end_line = int(end_line) if end_line is not None else 100
            except ValueError:
                end_line = 100
            
            # Ensure start_line <= end_line
            if start_line > end_line:
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
                                        if start_line <= total_lines <= end_line:
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
                                if total_lines > end_line:
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
                            if total_lines > end_line:
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
                        if total_lines > end_line:
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

            filter_html = f'''
            <form method="get" style="margin-bottom:10px;">
                <input type="hidden" name="path" value="{path}">
                <input type="text" name="filter" placeholder="Filter lines..." value="{filter_substring or ''}" style="width:200px;">
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
    def get_current_user(self) -> str | None:
        return self.get_secure_cookie("user")

    def check_origin(self, origin):
        # Only allow connections from the same host
        allowed_origins = [
            f"http://{self.request.host}",
            f"https://{self.request.host}",
            "http://localhost:8000",
            "http://127.0.0.1:8000"
        ]
        return origin in allowed_origins

    async def open(self, path):
        if not self.current_user:
            self.close()
            return

        path = path.lstrip('/')
        self.file_path = os.path.abspath(os.path.join(ROOT_DIR, path))
        self.running = True
        # Number of tail lines to send on connect
        try:
            n_param = self.get_query_argument('n', default='100')
            self.tail_n = int(n_param)
            if self.tail_n < 1:
                self.tail_n = 100
        except Exception:
            self.tail_n = 100
        if not os.path.isfile(self.file_path):
            await self.write_message(f"File not found: {self.file_path}")
            self.close()
            return

        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                last_n_lines = deque(f, self.tail_n)
            if last_n_lines:
                for line in last_n_lines:
                    await self.write_message(line)
        except Exception as e:
            await self.write_message(f"Error reading file history: {e}")

        try:
            self.file = open(self.file_path, 'r', encoding='utf-8', errors='replace')
            self.file.seek(0, os.SEEK_END)
        except Exception as e:
            await self.write_message(f"Error opening file for streaming: {e}")
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
            await self.write_message(line)
            where = self.file.tell()
            line = self.file.readline()
        self.file.seek(where)

    def on_close(self):
        self.running = False
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
        safe_dir_abs = os.path.abspath(os.path.join(ROOT_DIR, self.upload_dir.strip("/")))
        if not safe_dir_abs.startswith(ROOT_DIR):
            self.set_status(403)
            self.write("Forbidden path")
            return

        # Validate filename more strictly
        safe_filename = os.path.basename(self.filename)
        if not safe_filename or safe_filename in ['.', '..']:
            self.set_status(400)
            self.write("Invalid filename")
            return
            
        # Check for dangerous file extensions
        dangerous_extensions = ['.exe', '.bat', '.cmd', '.scr', '.vbs', '.js', '.jar']
        file_ext = os.path.splitext(safe_filename)[1].lower()
        if file_ext in dangerous_extensions:
            self.set_status(403)
            self.write("File type not allowed")
            return
            
        # Validate filename length
        if len(safe_filename) > 255:
            self.set_status(400)
            self.write("Filename too long")
            return

        final_path_abs = os.path.abspath(os.path.join(safe_dir_abs, safe_filename))
        if not final_path_abs.startswith(safe_dir_abs):
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
        if not abspath.startswith(root):
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
        if not (abspath.startswith(root) and new_abspath.startswith(root)):
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
        
        if not abspath.startswith(ROOT_DIR):
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
    def get(self, path):
        if not is_feature_enabled("file_edit", True):
            self.set_status(403)
            self.write("File editing is disabled.")
            return

        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not (abspath.startswith(ROOT_DIR)):
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
        
        # Use optimized file loading (sync version for template rendering)
        try:
            file_size = os.path.getsize(abspath)
            if MMapFileHandler.should_use_mmap(file_size):
                with open(abspath, 'rb') as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        full_file_content = mm[:].decode('utf-8', errors='replace')
            else:
                with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                    full_file_content = f.read()
        except (OSError, UnicodeDecodeError):
            with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                full_file_content = f.read()
                
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
        
        if not abspath.startswith(ROOT_DIR):
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
                if ap.startswith(ROOT_DIR) and os.path.isfile(ap):
                    valid_paths.append(p)
            if not valid_paths:
                self.set_status(400)
                self.write({"error": "No valid files"})
                return
            sid = secrets.token_urlsafe(8)
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
        if not (abspath.startswith(ROOT_DIR) and os.path.isfile(abspath)):
            self.set_status(404)
            self.write("File not found")
            return
        self.set_header('Content-Type', 'text/plain; charset=utf-8')
        # Stream in chunks to avoid loading entire file into memory
        try:
            # Prefer binary read and decode per chunk to preserve memory
            with open(abspath, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
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
        (r"/admin/login", AdminLoginHandler),
        (r"/admin", AdminHandler),
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
