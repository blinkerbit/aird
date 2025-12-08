import os
import json
import sqlite3
from datetime import datetime
import re
import weakref
import threading
import time
from urllib.parse import urlparse
import tornado.ioloop
import mmap
import chardet

MMAP_MIN_SIZE = 1024 * 1024 # 1MB
CHUNK_SIZE = 1024 * 1024 # 1MB

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
def join_path(*parts):
    return os.path.join(*parts).replace("\\", "/")

def is_within_root(path: str, root: str) -> bool:
    """Return True if path is within root after resolving symlinks and normalization."""
    try:
        path_real = os.path.realpath(path)
        root_real = os.path.realpath(root)
        return os.path.commonpath([path_real, root_real]) == root_real
    except Exception:
        return False

def format_size(size: int) -> str:
    """Format size in bytes to human readable string"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

def is_valid_websocket_origin(handler, origin: str) -> bool:
    """Validate WebSocket origin.
    Accept same-host origins and common localhost variants. Allow both http/https and ws/wss schemes.
    Require matching port unless the origin omits a port (treated as match).
    """
    try:
        if not origin:
            return False
        parsed = urlparse(origin)
        origin_host = parsed.hostname
        origin_port = parsed.port
        origin_scheme = (parsed.scheme or "").lower()

        if not origin_host:
            return False

        # Determine request host/port with safe defaults
        raw_host = getattr(handler.request, "host", "") or ""
        req_host = raw_host.split(":")[0] if raw_host else "localhost"
        # Default protocol http if missing on mock requests
        req_protocol = getattr(handler.request, "protocol", "http").lower()
        try:
            req_port = int(raw_host.split(":")[1])
        except (IndexError, ValueError):
            req_port = 443 if req_protocol == "https" else 80

        # Scheme check: allow http/https and ws/wss interchangeably
        allowed_schemes = {"http", "https", "ws", "wss"}
        if origin_scheme not in allowed_schemes:
            return False

        # Host check: exact match or localhost variants
        same_host = (origin_host == req_host)
        localhost_pair = origin_host in {"localhost", "127.0.0.1"} and req_host in {"localhost", "127.0.0.1"}
        if not (same_host or localhost_pair):
            return False
        return True
    except Exception:
        return False

class WebSocketConnectionManager:
    def __init__(self, config_prefix="ws", default_max_connections=100, default_idle_timeout=60):
        self.connections: set = set()
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
        from aird.main import get_current_websocket_config
        config = get_current_websocket_config()
        return config.get(f"{self.config_prefix}_max_connections", self.default_max_connections)
    
    @property
    def idle_timeout(self) -> int:
        """Get current idle timeout from configuration"""
        from aird.main import get_current_websocket_config
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

def get_all_files_recursive(root_path: str, base_path: str = "") -> list:
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
                sub_files = get_all_files_recursive(item_path, relative_path)
                all_files.extend(sub_files)
    except (OSError, PermissionError) as e:
        print(f"Error scanning directory {root_path}: {e}")
    
    return all_files

def matches_glob_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the given glob patterns"""
    if not patterns:
        return False
    
    import fnmatch
    for pattern in patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False

def filter_files_by_patterns(
    files: list[str], allow_list: list[str] = None, avoid_list: list[str] = None
) -> list[str]:
    """Filter files based on allow and avoid glob patterns."""
    if not files:
        return files

    filtered_files = []

    for file_path in files:
        # Check avoid list first (takes priority)
        if avoid_list and matches_glob_patterns(file_path, avoid_list):
            continue

        # Check allow list
        if allow_list:
            if matches_glob_patterns(file_path, allow_list):
                filtered_files.append(file_path)
        else:
            # No allow list means all files are allowed (unless in avoid list)
            filtered_files.append(file_path)

    return filtered_files

def cloud_root_dir() -> str:
    from aird.constants import ROOT_DIR, CLOUD_SHARE_FOLDER
    return os.path.join(ROOT_DIR, CLOUD_SHARE_FOLDER)


def ensure_share_cloud_dir(share_id: str) -> str:
    share_dir = os.path.join(cloud_root_dir(), share_id)
    os.makedirs(share_dir, exist_ok=True)
    return share_dir


def sanitize_cloud_filename(name: str | None) -> str:
    candidate = (name or "cloud_file").strip()
    candidate = candidate.replace(os.sep, "_").replace("/", "_")
    candidate = re.sub(r"[^A-Za-z0-9._-]", "_", candidate)
    candidate = candidate.strip("._")
    if not candidate:
        candidate = "cloud_file"
    return candidate[:128]


def is_cloud_relative_path(share_id: str, relative_path: str) -> bool:
    from aird.constants import CLOUD_SHARE_FOLDER
    normalized = relative_path.replace("\\", "/")
    prefix = f"{CLOUD_SHARE_FOLDER}/{share_id}/"
    return normalized.startswith(prefix)


def remove_cloud_file_if_exists(share_id: str, relative_path: str) -> None:
    from aird.constants import ROOT_DIR
    if not is_cloud_relative_path(share_id, relative_path):
        return
    abs_path = os.path.abspath(os.path.join(ROOT_DIR, relative_path))
    if not is_within_root(abs_path, ROOT_DIR):
        return
    if os.path.isfile(abs_path):
        try:
            os.remove(abs_path)
        except OSError:
            pass
    cleanup_share_cloud_dir_if_empty(share_id)


def cleanup_share_cloud_dir_if_empty(share_id: str) -> None:
    import shutil
    share_dir = os.path.join(cloud_root_dir(), share_id)
    try:
        if os.path.isdir(share_dir) and not os.listdir(share_dir):
            shutil.rmtree(share_dir, ignore_errors=True)
    except Exception:
        pass


def remove_share_cloud_dir(share_id: str) -> None:
    import shutil
    if not share_id:
        return
    share_dir = os.path.join(cloud_root_dir(), share_id)
    shutil.rmtree(share_dir, ignore_errors=True)


def download_cloud_item(share_id: str, item: dict) -> str:
    from aird.cloud import CloudProviderError, CLOUD_MANAGER
    provider_name = item.get("provider")
    file_id = item.get("id")
    if not provider_name or not file_id:
        raise CloudProviderError("Invalid cloud file specification")
    if item.get("is_dir"):
        raise CloudProviderError("Cloud folder sharing is not supported")
    provider = CLOUD_MANAGER.get(provider_name)
    if not provider:
        raise CloudProviderError(f"Cloud provider '{provider_name}' is not configured")
    try:
        download = provider.download_file(file_id)
    except CloudProviderError:
        raise
    except Exception as exc:
        raise CloudProviderError(str(exc)) from exc

    filename = sanitize_cloud_filename(item.get("name") or getattr(download, "name", None) or f"{provider_name}-{file_id}")
    share_dir = ensure_share_cloud_dir(share_id)
    base, ext = os.path.splitext(filename)
    candidate = filename
    dest_path = os.path.join(share_dir, candidate)
    counter = 1
    while os.path.exists(dest_path):
        candidate = f"{base}_{counter}{ext}"
        dest_path = os.path.join(share_dir, candidate)
        counter += 1

    try:
        with open(dest_path, "wb") as fh:
            for chunk in download.iter_chunks():
                fh.write(chunk)
    except Exception as exc:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise CloudProviderError(f"Failed to download cloud file '{filename}': {exc}") from exc
    finally:
        try:
            download.close()
        except Exception:
            pass
    from aird.constants import CLOUD_SHARE_FOLDER
    relative = join_path(CLOUD_SHARE_FOLDER, share_id, os.path.basename(dest_path))
    return relative


def download_cloud_items(share_id: str, items: list[dict]) -> list[str]:
    from aird.cloud import CloudProviderError
    if not items:
        return []
    downloaded: list[str] = []
    try:
        for item in items:
            downloaded.append(download_cloud_item(share_id, item))
        return downloaded
    except CloudProviderError:
        for rel_path in downloaded:
            remove_cloud_file_if_exists(share_id, rel_path)
        raise
    except Exception as exc:
        for rel_path in downloaded:
            remove_cloud_file_if_exists(share_id, rel_path)
        raise CloudProviderError(str(exc)) from exc

# Dummy implementations for parse_expression and evaluate_expression
# These should be replaced with a real implementation if complex filtering is needed.
def parse_expression(expression: str):
    """
    Parses a filter expression.
    For now, it returns a simple structure that `evaluate_expression` can use.
    """
    return {"raw": expression}

def evaluate_expression(line: str, parsed_expr: dict) -> bool:
    """
    Evaluates a parsed expression against a line of text.
    For now, it performs a simple case-insensitive substring search.
    """
    term = parsed_expr.get("raw", "")
    if not term:
        return True  # No filter means everything matches
    return term.lower() in line.lower()

def get_current_feature_flags() -> dict:
    """Return current feature flags with in-memory changes taking precedence over DB.
    This ensures real-time updates are immediately reflected.
    Falls back to in-memory defaults if DB is unavailable.
    """
    from aird.constants import FEATURE_FLAGS
    import aird.constants as constants_module
    from aird.db import _load_feature_flags

    # Start with in-memory flags (which may have just been updated)
    current = FEATURE_FLAGS.copy()
    
    db_conn = constants_module.DB_CONN
    if db_conn is not None:
        try:
            persisted = _load_feature_flags(db_conn)
            if persisted:
                # Start with DB values as base
                merged = persisted.copy()
                # Then overlay in-memory changes (in-memory takes precedence for real-time updates)
                for k, v in current.items():
                    merged[k] = bool(v)
                # Also include any DB-only flags
                for k, v in persisted.items():
                    if k not in merged:
                        merged[k] = bool(v)
                return merged
        except Exception:
            pass
    return current

def get_current_websocket_config() -> dict:
    """Return current WebSocket configuration with SQLite values taking precedence.
    Falls back to in-memory defaults if DB is unavailable.
    """
    from aird.constants import WEBSOCKET_CONFIG
    from aird.db import DB_CONN, _load_websocket_config

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
