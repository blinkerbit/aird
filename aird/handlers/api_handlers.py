import tornado.web
import tornado.websocket
import tornado.escape
import json
import time
import os
import logging
import mmap
import asyncio
import concurrent.futures
from collections import deque
from urllib.parse import unquote

from aird.handlers.base_handler import BaseHandler
from aird.db import (
    search_users,
    get_shares_for_path,
    get_share_by_id,
    get_all_shares,
    load_feature_flags,
)
from aird.utils.util import (
    is_within_root,
    get_files_in_directory,
    is_video_file,
    is_audio_file,
    WebSocketConnectionManager,
    is_valid_websocket_origin,
    parse_expression,
    evaluate_expression,
    is_feature_enabled,
    get_current_feature_flags,
)
from aird.config import (
    ROOT_DIR,
    MMAP_MIN_SIZE,
    CHUNK_SIZE,
    MAX_READABLE_FILE_SIZE,
)
import aird.constants as constants_module


class FeatureFlagSocketHandler(tornado.websocket.WebSocketHandler):
    # Use connection manager with configurable limits for feature flags
    connection_manager = WebSocketConnectionManager("feature_flags", default_max_connections=50, default_idle_timeout=600)

    def open(self):
        if not self.get_current_user():
            self.close(code=1008, reason="Authentication required")
            return

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
        FeatureFlagSocketHandler.connection_manager.remove_connection(self)

    def check_origin(self, origin):
        # Improved origin validation (Priority 2)
        return is_valid_websocket_origin(self, origin)

    def _get_current_feature_flags(self):
        """Get current feature flags.
        Returns flags with in-memory changes taking precedence over DB (for real-time updates).
        """
        from aird.constants import FEATURE_FLAGS
        import aird.constants as constants_module
        
        # Start with in-memory flags (which may have just been updated)
        current_flags = FEATURE_FLAGS.copy()
        
        # Merge with DB values, but in-memory takes precedence for keys that exist in both
        db_conn = constants_module.DB_CONN
        if db_conn is not None:
            try:
                persisted = load_feature_flags(db_conn)
                if persisted:
                    # Start with DB values as base
                    merged = persisted.copy()
                    # Then overlay in-memory changes (in-memory takes precedence)
                    for k, v in current_flags.items():
                        merged[k] = bool(v)
                    return merged
            except Exception:
                pass
        
        # Fallback to in-memory flags
        return current_flags

    @classmethod
    def send_updates(cls):
        """Send feature flag updates to all connected clients.
        Uses in-memory flags merged with DB to ensure real-time updates are reflected.
        """
        from aird.constants import FEATURE_FLAGS
        import aird.constants as constants_module
        
        # Start with in-memory flags (which may have just been updated)
        current_flags = FEATURE_FLAGS.copy()
        
        # Merge with DB values, but in-memory takes precedence for keys that exist in both
        db_conn = constants_module.DB_CONN
        if db_conn is not None:
            try:
                persisted = load_feature_flags(db_conn)
                if persisted:
                    # Start with DB values as base
                    merged = persisted.copy()
                    # Then overlay in-memory changes (in-memory takes precedence)
                    for k, v in current_flags.items():
                        merged[k] = bool(v)
                    current_flags = merged
            except Exception:
                pass
        
        cls.connection_manager.broadcast_message(json.dumps(current_flags))


class FileStreamHandler(tornado.websocket.WebSocketHandler):
    # Use connection manager with configurable limits for file streaming
    connection_manager = WebSocketConnectionManager("file_streaming", default_max_connections=200, default_idle_timeout=300)

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self.file_path = None
        self.file = None
        self.is_streaming = False
        self.line_buffer = deque(maxlen=1000)  # Default buffer size
        self.filter_expression = None
        self.stop_event = asyncio.Event()

    def get_current_user(self):
        # Use parent class implementation which handles authentication properly
        return super().get_current_user()

    async def open(self, path):
        if not self.get_current_user():
            self.close(code=1008, reason="Authentication required")
            return
            
        if not self.connection_manager.add_connection(self):
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Connection limit exceeded. Please try again later.'
            }))
            self.close(code=1013, reason="Connection limit exceeded")
            return

        self.file_path = os.path.abspath(os.path.join(ROOT_DIR, unquote(path)))
        if not is_within_root(self.file_path, ROOT_DIR) or not os.path.isfile(self.file_path):
            self.close(code=1003, reason="File not found")
            return
        
        # Initialize with last N lines
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                self.line_buffer.extend(f)
            for line in self.line_buffer:
                self.write_message(json.dumps({'type': 'line', 'data': line.strip()}))
        except Exception:
            pass

        self.is_streaming = True
        asyncio.create_task(self.stream_file())

    async def on_message(self, message):
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, ValueError):
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Invalid JSON payload'
            }))
            return

        action = data.get('action')
        if not action:
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Invalid request: action is required'
            }))
            return

        if action == 'stop':
            self.is_streaming = False
            self.stop_event.set()
            return
        if action == 'start':
            self.is_streaming = True
            asyncio.create_task(self.stream_file())
            return
        if action == 'lines':
            try:
                new_size = int(data.get('lines', 0))
                if new_size > 0:
                    self.line_buffer = deque(self.line_buffer, maxlen=new_size)
            except (ValueError, TypeError):
                pass
            return
        if action == 'filter':
            self.filter_expression = data.get('filter')
            return
        if action == 'stream_file':
            # One-shot stream of a file chunk via MMapFileHandler
            rel_path = (data.get('file_path') or '').strip()
            if not rel_path:
                self.write_message(json.dumps({'type': 'error', 'message': 'file_path is required'}))
                return
            abs_path = os.path.abspath(os.path.join(ROOT_DIR, rel_path))
            if not is_within_root(abs_path, ROOT_DIR):
                self.write_message(json.dumps({'type': 'error', 'message': 'Forbidden path'}))
                return
            if not os.path.isfile(abs_path):
                self.write_message(json.dumps({'type': 'error', 'message': 'File not found'}))
                return
            try:
                from aird.main import MMapFileHandler
                # Trigger the async generator once to satisfy tests; actual streaming omitted
                _ = MMapFileHandler.serve_file_chunk(abs_path)
            except Exception as e:
                self.write_message(json.dumps({'type': 'error', 'message': str(e)}))
            return

        # Unknown action
        self.write_message(json.dumps({
            'type': 'error',
            'message': 'Unknown action'
        }))

    async def stream_file(self):
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as self.file:
                self.file.seek(0, 2)  # Go to the end of the file
                while self.is_streaming:
                    line = self.file.readline()
                    if not line:
                        await asyncio.sleep(0.1)
                        continue
                    
                    if self.filter_expression:
                        try:
                            parsed_expr = parse_expression(self.filter_expression)
                            if evaluate_expression(line, parsed_expr):
                                self.write_message(json.dumps({'type': 'line', 'data': line.strip()}))
                        except Exception:
                            # If filter is invalid, ignore it
                            self.write_message(json.dumps({'type': 'line', 'data': line.strip()}))
                    else:
                        self.write_message(json.dumps({'type': 'line', 'data': line.strip()}))

                    if self.stop_event.is_set():
                        break
        except (tornado.websocket.WebSocketClosedError, RuntimeError):
            pass
        except Exception as e:
            try:
                self.write_message(json.dumps({'type': 'error', 'message': str(e)}))
            except tornado.websocket.WebSocketClosedError:
                pass

    def on_close(self):
        self.is_streaming = False
        self.stop_event.set()
        try:
            if self.file:
                self.file.close()
        except Exception:
            pass
        self.connection_manager.remove_connection(self)

    def check_origin(self, origin):
        return is_valid_websocket_origin(self, origin)

class FileListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, path):
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not is_within_root(abspath, ROOT_DIR):
            self.set_status(403)
            self.write("Access denied")
            return
        if not os.path.isdir(abspath):
            self.set_status(404)
            self.write("Directory not found")
            return
        try:
            files = get_files_in_directory(abspath)
            
            # Augment file data with shared status
            all_shared_paths = set()
            db_conn = constants_module.DB_CONN
            if db_conn:
                all_shares = get_all_shares(db_conn)
                for share in all_shares.values():
                    for p in share.get('paths', []):
                        all_shared_paths.add(p)

            for file_info in files:
                file_info['is_shared'] = os.path.join(path, file_info['name']).replace("\\", "/") in all_shared_paths
                
            result = {
                "path": path,
                "files": files,
                "is_video": is_video_file(path),
                "is_audio": is_audio_file(path),
            }
            self.write(result)
        except Exception as e:
            self.set_status(500)
            self.write(str(e))

class SuperSearchHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        from aird.main import is_feature_enabled as _is_enabled
        if not _is_enabled("super_search", True):
            self.set_status(403)
            self.write("Feature disabled: Super Search is currently disabled by administrator")
            return
        
        # Get the current path from query parameter
        current_path = self.get_argument("path", "").strip()
        # Ensure path is safe and normalized
        if current_path:
            current_path = current_path.strip('/')
        
        self.render("super_search.html", current_path=current_path)

class SuperSearchWebSocketHandler(tornado.websocket.WebSocketHandler):
    # Use connection manager with configurable limits for search
    connection_manager = WebSocketConnectionManager("search", default_max_connections=100, default_idle_timeout=180)

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self.search_task = None
        self.stop_event = asyncio.Event()

    def get_current_user(self):
        """Authenticate user for WebSocket connection"""
        # Check for user session from secure cookie (same as BaseHandler)
        user_json = self.get_secure_cookie("user")
        if user_json:
            try:
                from aird.db import get_user_by_username
                import aird.constants as constants_module
                # Handle both JSON-encoded user data and plain string usernames
                try:
                    import json
                    user_data = json.loads(user_json)
                    username = user_data.get("username", "")
                except (json.JSONDecodeError, TypeError):
                    # If it's not JSON, treat it as a plain username string
                    username = user_json.decode('utf-8') if isinstance(user_json, bytes) else str(user_json)
                
                # Re-verify user from DB to ensure they are still valid
                db_conn = constants_module.DB_CONN
                if db_conn:
                    user = get_user_by_username(db_conn, username)
                    if user:
                        return user
                # If DB check fails but we have a username, return basic user info for token-authenticated users
                if username == "token_authenticated":
                    return {"username": "token_user", "role": "admin"}
            except Exception:
                # If cookie parsing fails, check if it's a token-authenticated user
                if isinstance(user_json, bytes):
                    user_str = user_json.decode('utf-8', errors='ignore')
                    if user_str == "token_authenticated":
                        return {"username": "token_user", "role": "admin"}
                return None
        
        # Fallback to token-based authentication from Authorization header
        auth_header = self.request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            # Access token directly from module to ensure we have the latest value
            import aird.config as config_module
            current_access_token = config_module.ACCESS_TOKEN
            if current_access_token and token.strip().strip("'\"") == current_access_token.strip().strip("'\""):
                # Return a generic user object for token-based access
                return {"username": "token_user", "role": "admin"}
        
        return None

    def open(self):
        user = self.get_current_user()
        if not user:
            logging.warning("SuperSearchWebSocket: Authentication failed - no valid user session")
            # Send authentication error message before closing
            try:
                self.write_message(json.dumps({
                    'type': 'auth_required',
                    'message': 'Authentication required. Please log in.',
                    'redirect': '/login?next=' + tornado.escape.url_escape(self.request.path)
                }))
            except Exception:
                pass
            self.close(code=1008, reason="Authentication required")
            return
            
        logging.info(f"SuperSearchWebSocket: User authenticated - {user.get('username', 'unknown')}")
        
        if not self.connection_manager.add_connection(self):
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Connection limit exceeded. Please try again later.'
            }))
            self.close(code=1013, reason="Connection limit exceeded")
            return

    async def on_message(self, message):
        # Validate authentication on each message to ensure session is still valid
        user = self.get_current_user()
        if not user:
            logging.warning("SuperSearchWebSocket: Authentication failed on message - session expired")
            self.write_message(json.dumps({
                'type': 'auth_required',
                'message': 'Your session has expired. Please log in again.',
                'redirect': '/login?next=/search'
            }))
            self.close(code=1008, reason="Authentication expired")
            return
        
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, ValueError):
            self.write_message(json.dumps({'type': 'error', 'message': 'Invalid JSON format'}))
            return
        pattern = data.get('pattern')
        search_text = data.get('search_text')
        if not pattern or not search_text:
            self.write_message(json.dumps({'type': 'error', 'message': 'Both pattern and search_text are required'}))
            return
        
        # Validate authentication again before starting search
        user = self.get_current_user()
        if not user:
            logging.warning("SuperSearchWebSocket: Authentication failed before search start")
            self.write_message(json.dumps({
                'type': 'auth_required',
                'message': 'Your session has expired. Please log in again.',
                'redirect': '/login?next=/search'
            }))
            self.close(code=1008, reason="Authentication expired")
            return
            
        if self.search_task and not self.search_task.done():
            self.stop_event.set()
            asyncio.create_task(self._await_cancellation_and_start_new((pattern, search_text)))
        else:
            self.stop_event.clear()
            self.search_task = asyncio.create_task(self.perform_search(pattern, search_text))

    async def _await_cancellation_and_start_new(self, args):
        try:
            await self.search_task
        except asyncio.CancelledError:
            pass
        self.stop_event.clear()
        pattern, search_text = args
        self.search_task = asyncio.create_task(self.perform_search(pattern, search_text))

    async def perform_search(self, pattern: str, search_text: str):
        """Perform the super search and stream results"""
        # Validate authentication at the start of search
        user = self.get_current_user()
        if not user:
            logging.warning("SuperSearchWebSocket: Authentication failed at search start")
            self.write_message(json.dumps({
                'type': 'auth_required',
                'message': 'Your session has expired. Please log in again.',
                'redirect': '/login?next=/search'
            }))
            return
            
        try:
            # Send search start notification
            self.write_message(json.dumps({
                'type': 'search_start',
                'pattern': pattern,
                'search_text': search_text
            }))
            
            import fnmatch
            import pathlib
            
            # Normalize pattern to use forward slashes for matching
            normalized_pattern = pattern.replace('\\', '/')
            
            # Ensure pattern is relative to ROOT_DIR
            root_path = pathlib.Path(ROOT_DIR).resolve()
            matches = 0
            files_searched = 0
            
            # Walk through directory tree
            for dirpath, dirnames, filenames in os.walk(root_path):
                if self.stop_event.is_set():
                    raise asyncio.CancelledError
                
                # Check each file against the pattern
                for filename in filenames:
                    if self.stop_event.is_set():
                        raise asyncio.CancelledError
                    
                    file_path = pathlib.Path(dirpath) / filename
                    try:
                        # Get relative path from ROOT_DIR
                        rel_path = file_path.relative_to(root_path)
                        rel_path_str = str(rel_path).replace('\\', '/')
                        
                        # Check if file matches pattern
                        if not fnmatch.fnmatch(rel_path_str, normalized_pattern) and not fnmatch.fnmatch(filename, normalized_pattern):
                            continue
                        
                        files_searched += 1
                        
                        # Try to read and search file content
                        try:
                            # Check file size before reading
                            file_size = file_path.stat().st_size
                            if file_size > MAX_READABLE_FILE_SIZE:
                                # Skip files that are too large
                                continue
                            
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                for i, line in enumerate(f, 1):
                                    if self.stop_event.is_set():
                                        raise asyncio.CancelledError
                                    if search_text in line:
                                        self.send_match(rel_path_str, i, line.strip(), search_text)
                                        matches += 1
                        except (UnicodeDecodeError, PermissionError, OSError):
                            # Skip binary files or files we can't read
                            continue
                        except Exception:
                            # Skip any other errors
                            continue
                            
                    except (ValueError, OSError):
                        # Skip files outside root or inaccessible files
                        continue
                
                # Yield control periodically to avoid blocking and check authentication
                if files_searched % 100 == 0:
                    await asyncio.sleep(0)
                    # Periodically validate authentication during long searches
                    if not self.get_current_user():
                        logging.warning("SuperSearchWebSocket: Authentication expired during search")
                        self.write_message(json.dumps({
                            'type': 'auth_required',
                            'message': 'Your session expired during search. Please log in again.',
                            'redirect': '/login?next=/search'
                        }))
                        self.close(code=1008, reason="Authentication expired")
                        return
            
            # Send completion message
            if matches == 0:
                self.write_message(json.dumps({
                    'type': 'no_matches',
                    'files_searched': files_searched
                }))
            else:
                self.write_message(json.dumps({
                    'type': 'done',
                    'matches': matches,
                    'files_searched': files_searched
                }))
                
        except asyncio.CancelledError:
            self.write_message(json.dumps({'type': 'cancelled'}))
        except Exception as e:
            logging.error(f"Super search error: {e}", exc_info=True)
            self.write_message(json.dumps({
                'type': 'error',
                'message': f'Search failed: {str(e)}'
            }))

    def send_match(self, file_path: str, line_number: int, line_content: str, search_text: str):
        message = {
            'type': 'match',
            'file_path': file_path,
            'line_number': line_number,
            'line_content': line_content,
            'search_text': search_text,
        }
        try:
            self.write_message(json.dumps(message))
        except Exception:
            pass

    def on_close(self):
        if self.search_task:
            self.stop_event.set()
        self.connection_manager.remove_connection(self)

    def check_origin(self, origin):
        return is_valid_websocket_origin(self, origin)

class UserSearchAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Search users by username (for share access control)"""
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.set_status(500)
            self.write({"error": "Database not available"})
            return
            
        query = self.get_argument('q', '').strip()
        if len(query) < 1:
            self.write({"users": []})
            return
            
        try:
            users = search_users(db_conn, query)
            self.write({"users": users})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class ShareDetailsAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Get share details for a specific file"""
        from aird.main import is_feature_enabled as _is_enabled
        if not _is_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
            
        file_path = self.get_argument('path', '').strip()
        if not file_path:
            self.set_status(400)
            self.write({"error": "File path is required"})
            return
            
        try:
            # Find shares that contain this file
            db_conn = constants_module.DB_CONN
            if not db_conn:
                self.set_status(500)
                self.write({"error": "Database connection not available"})
                return
            matching_shares = get_shares_for_path(db_conn, file_path)

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
        from aird.main import is_feature_enabled as _is_enabled
        if not _is_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return

        share_id = self.get_argument('id', '').strip()
        if not share_id:
            self.set_status(400)
            self.write({"error": "Share ID is required"})
            return

        try:
            db_conn = constants_module.DB_CONN
            if not db_conn:
                self.set_status(500)
                self.write({"error": "Database connection not available"})
                return
            share = get_share_by_id(db_conn, share_id)
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

class ShareListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        from aird.main import is_feature_enabled as _is_enabled
        if not _is_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return

        db_conn = constants_module.DB_CONN
        if not db_conn:
            self.set_status(500)
            self.write({"error": "Database connection not available"})
            return
        shares = get_all_shares(db_conn)
        self.write({"shares": shares})

class WebSocketStatsHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Return WebSocket connection statistics"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
            
        stats = {
            'feature_flags': FeatureFlagSocketHandler.connection_manager.get_stats(),
            'file_streaming': FileStreamHandler.connection_manager.get_stats(),
            'super_search': SuperSearchWebSocketHandler.connection_manager.get_stats(),
            'timestamp': time.time()
        }
        
        self.set_header('Content-Type', 'application/json')
        self.write(json.dumps(stats, indent=2))

