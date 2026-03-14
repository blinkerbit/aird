import base64
import fnmatch
import json
import logging
import mimetypes
import os
import pathlib
import re
import time
import tornado.escape
import tornado.web
import tornado.websocket
import asyncio
import aiofiles
from collections import deque
from urllib.parse import unquote

from aird.handlers.constants import (
    AUTH_REQUIRED,
    AUTH_EXPIRED,
    REDIRECT_SEARCH,
    FILESHARE_DISABLED_MSG,
    DB_NOT_AVAILABLE_MSG,
)
from aird.handlers.base_handler import (
    BaseHandler,
    ManagedWebSocketMixin,
    authenticate_handler,
    require_admin,
    require_db,
)
from aird.db import (
    get_share_by_id,
    get_all_shares,
    search_users,
    get_shares_for_path,
)

from aird.utils.util import (
    get_files_in_directory,
    is_video_file,
    is_audio_file,
    WebSocketConnectionManager,
    is_feature_enabled,
    get_current_feature_flags,
    augment_with_shared_status,
)
from aird.core.security import (
    is_within_root,
    is_valid_websocket_origin,
)
from aird.core.filter_expression import FilterExpression
from aird.config import (
    ROOT_DIR,
    MAX_READABLE_FILE_SIZE,
)
from aird.core.mmap_handler import MMapFileHandler


class FeatureFlagSocketHandler(
    ManagedWebSocketMixin, tornado.websocket.WebSocketHandler
):
    # Use connection manager with configurable limits for feature flags
    connection_manager = WebSocketConnectionManager(
        "feature_flags", default_max_connections=50, default_idle_timeout=600
    )

    def open(self):
        if not self.get_current_user():
            self.close(code=1008, reason=AUTH_REQUIRED)
            return

        if not self.register_connection():
            return

        # Load current feature flags from SQLite and send to client
        current_flags = self._get_current_feature_flags()
        self.write_message(json.dumps(current_flags))

    def check_origin(self, origin):
        # Improved origin validation (Priority 2)
        return is_valid_websocket_origin(self, origin)

    def _get_current_feature_flags(self):
        """Get current feature flags using the consolidated implementation."""
        return get_current_feature_flags()

    @classmethod
    def send_updates(cls):
        """Send feature flag updates to all connected clients."""
        current_flags = get_current_feature_flags()
        cls.connection_manager.broadcast_message(json.dumps(current_flags))


class FileStreamHandler(ManagedWebSocketMixin, tornado.websocket.WebSocketHandler):
    # Use connection manager with configurable limits for file streaming
    connection_manager = WebSocketConnectionManager(
        "file_streaming", default_max_connections=200, default_idle_timeout=300
    )

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self.file_path = None
        self.file = None
        self.is_streaming = False
        self.line_buffer = deque(maxlen=1000)  # Default buffer size
        self.filter_expression = None
        self.stop_event = asyncio.Event()

    def get_current_user(self):
        # WebSocket handler doesn't have a default auth mechanism, so we explicitly call authenticate_handler
        return authenticate_handler(self)

    async def open(self, path):
        if not self.get_current_user():
            self.close(code=1008, reason=AUTH_REQUIRED)
            return

        if not self.register_connection():
            return

        self.file_path = os.path.abspath(os.path.join(ROOT_DIR, unquote(path)))
        if not is_within_root(self.file_path, ROOT_DIR) or not os.path.isfile(
            self.file_path
        ):
            self.close(code=1003, reason="File not found")
            return

        n_str = self.get_argument("n", "1000")
        try:
            n_lines = int(n_str)
            if n_lines <= 0:
                n_lines = 1000
        except ValueError:
            n_lines = 1000

        self.line_buffer = deque(maxlen=n_lines)
        self.filter_expression = self.get_argument("filter", None)

        expr = None
        if self.filter_expression:
            try:
                expr = FilterExpression(self.filter_expression)
            except (ValueError, TypeError, re.error) as parse_err:
                logging.debug(
                    "Invalid filter expression %s: %s",
                    self.filter_expression,
                    parse_err,
                )

        # Initialize with last N matching lines (async, non-blocking)
        try:
            async with aiofiles.open(
                self.file_path, "r", encoding="utf-8", errors="replace"
            ) as f:
                async for line in f:
                    if expr and not expr.matches(line):
                        continue
                    self.line_buffer.append(line)
            for line in self.line_buffer:
                await self.write_message(
                    json.dumps({"type": "line", "data": line.strip()})
                )
        except Exception as e:
            logging.error(f"Error reading file: {self.file_path}", exc_info=True)
            self.write_message(json.dumps({"type": "error", "message": str(e)}))
            return

        self.is_streaming = True
        asyncio.create_task(self.stream_file())

    async def _handle_stream_file_action(self, data: dict) -> None:
        """Handle the stream_file action."""
        rel_path = (data.get("file_path") or "").strip()
        if not rel_path:
            self.write_message(
                json.dumps({"type": "error", "message": "file_path is required"})
            )
            return
        abs_path = os.path.abspath(os.path.join(ROOT_DIR, rel_path))
        if not is_within_root(abs_path, ROOT_DIR):
            self.write_message(
                json.dumps({"type": "error", "message": "Forbidden path"})
            )
            return
        if not os.path.isfile(abs_path):
            self.write_message(
                json.dumps({"type": "error", "message": "File not found"})
            )
            return
        try:
            content_type = (
                mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
            )
            file_size = os.path.getsize(abs_path)
            await self.write_message(
                json.dumps(
                    {
                        "type": "stream_start",
                        "file_path": rel_path,
                        "content_type": content_type,
                        "size": file_size,
                    }
                )
            )
            async for chunk in MMapFileHandler.serve_file_chunk(abs_path):
                await self.write_message(
                    json.dumps(
                        {
                            "type": "stream_data",
                            "data": base64.b64encode(chunk).decode("ascii"),
                        }
                    )
                )
            await self.write_message(json.dumps({"type": "stream_end"}))
        except Exception as e:
            self.write_message(json.dumps({"type": "error", "message": str(e)}))

    async def on_message(self, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            self.write_message(
                json.dumps({"type": "error", "message": "Invalid JSON payload"})
            )
            return

        action = data.get("action")
        if not action:
            self.write_message(
                json.dumps(
                    {"type": "error", "message": "Invalid request: action is required"}
                )
            )
            return

        if action == "stop":
            self.is_streaming = False
            self.stop_event.set()
            return
        if action == "start":
            self.is_streaming = True
            asyncio.create_task(self.stream_file())
            return
        if action == "lines":
            try:
                new_size = int(data.get("lines", 0))
                if new_size > 0:
                    self.line_buffer = deque(self.line_buffer, maxlen=new_size)
            except (ValueError, TypeError):
                pass
            return
        if action == "filter":
            self.filter_expression = data.get("filter")
            return
        if action == "stream_file":
            await self._handle_stream_file_action(data)
            return

        self.write_message(json.dumps({"type": "error", "message": "Unknown action"}))

    async def _send_line_with_filter(
        self, line: str, expr, filter_str: str, last_filter_str
    ):
        """Apply filter and send line; returns (expr, last_filter_str) for next call."""
        if filter_str and filter_str != last_filter_str:
            try:
                expr = FilterExpression(filter_str)
            except (ValueError, TypeError, re.error):
                expr = None
            last_filter_str = filter_str

        should_send = True
        if filter_str and expr:
            try:
                should_send = expr.matches(line)
            except Exception as match_err:
                logging.debug(
                    "Filter match error for line, sending anyway: %s", match_err
                )
        if should_send:
            await self.write_message(json.dumps({"type": "line", "data": line.strip()}))
        return expr, last_filter_str

    async def stream_file(self):
        expr = None
        last_filter_str = None
        try:
            async with aiofiles.open(
                self.file_path, "r", encoding="utf-8", errors="replace"
            ) as self.file:
                await self.file.seek(0, 2)  # Go to the end of the file
                while self.is_streaming:
                    line = await self.file.readline()
                    if not line:
                        await asyncio.sleep(0.1)
                        continue
                    expr, last_filter_str = await self._send_line_with_filter(
                        line, expr, self.filter_expression or "", last_filter_str
                    )
                    if self.stop_event.is_set():
                        break
        except (tornado.websocket.WebSocketClosedError, RuntimeError):
            pass
        except Exception as e:
            try:
                await self.write_message(
                    json.dumps({"type": "error", "message": str(e)})
                )
            except tornado.websocket.WebSocketClosedError:
                pass

    def on_close(self):
        self.is_streaming = False
        self.stop_event.set()
        try:
            if self.file:
                self.file.close()
        except OSError as close_err:
            logging.debug("Error closing file stream: %s", close_err)
        super().on_close()

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
            db_conn = self.db_conn
            if db_conn:
                augment_with_shared_status(files, path, get_all_shares(db_conn))

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
        if not is_feature_enabled("super_search", True):
            self.set_status(403)
            self.write(
                "Feature disabled: Super Search is currently disabled by administrator"
            )
            return

        # Get the current path from query parameter
        current_path = self.get_argument("path", "").strip()
        # Ensure path is safe and normalized
        if current_path:
            current_path = current_path.strip("/")

        self.render("super_search.html", current_path=current_path)


class SuperSearchWebSocketHandler(
    ManagedWebSocketMixin, tornado.websocket.WebSocketHandler
):
    # Use connection manager with configurable limits for search
    connection_manager = WebSocketConnectionManager(
        "search", default_max_connections=100, default_idle_timeout=180
    )

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self.search_task = None
        self.stop_event = asyncio.Event()

    def get_current_user(self):
        """Authenticate user for WebSocket connection"""
        return authenticate_handler(self)

    def _send_auth_required_and_close(
        self, message: str, redirect: str, close_reason: str
    ):
        """Send auth_required JSON and close the WebSocket."""
        try:
            self.write_message(
                json.dumps(
                    {
                        "type": "auth_required",
                        "message": message,
                        "redirect": redirect,
                    }
                )
            )
        except (tornado.websocket.WebSocketClosedError, RuntimeError):
            pass
        self.close(code=1008, reason=close_reason)

    def open(self):
        user = self.get_current_user()
        if not user:
            logging.warning(
                "SuperSearchWebSocket: Authentication failed - no valid user session"
            )
            self._send_auth_required_and_close(
                "Authentication required. Please log in.",
                "/login?next=" + tornado.escape.url_escape(self.request.path),
                AUTH_REQUIRED,
            )
            return

        logging.info(
            f"SuperSearchWebSocket: User authenticated - {user.get('username', 'unknown')}"
        )

        if not self.register_connection():
            return

    async def on_message(self, message):
        # Validate authentication on each message to ensure session is still valid
        user = self.get_current_user()
        if not user:
            logging.warning(
                "SuperSearchWebSocket: Authentication failed on message - session expired"
            )
            self._send_auth_required_and_close(
                AUTH_EXPIRED, REDIRECT_SEARCH, AUTH_EXPIRED
            )
            return

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            self.write_message(
                json.dumps({"type": "error", "message": "Invalid JSON format"})
            )
            return
        pattern = data.get("pattern")
        search_text = data.get("search_text")
        if not pattern or not search_text:
            self.write_message(
                json.dumps(
                    {
                        "type": "error",
                        "message": "Both pattern and search_text are required",
                    }
                )
            )
            return

        # Validate authentication again before starting search
        user = self.get_current_user()
        if not user:
            logging.warning(
                "SuperSearchWebSocket: Authentication failed before search start"
            )
            self._send_auth_required_and_close(
                AUTH_EXPIRED, REDIRECT_SEARCH, AUTH_EXPIRED
            )
            return

        if self.search_task and not self.search_task.done():
            self.stop_event.set()
            asyncio.create_task(
                self._await_cancellation_and_start_new((pattern, search_text))
            )
        else:
            self.stop_event.clear()
            self.search_task = asyncio.create_task(
                self.perform_search(pattern, search_text)
            )

    async def _await_cancellation_and_start_new(self, args):
        try:
            await self.search_task
        except asyncio.CancelledError:
            self.stop_event.clear()
            pattern, search_text = args
            self.search_task = asyncio.create_task(
                self.perform_search(pattern, search_text)
            )
            raise
        self.stop_event.clear()
        pattern, search_text = args
        self.search_task = asyncio.create_task(
            self.perform_search(pattern, search_text)
        )

    @staticmethod
    def _file_matches_pattern(
        rel_path_str: str, filename: str, normalized_pattern: str
    ) -> bool:
        """Return True if the file path or filename matches the glob pattern."""
        return fnmatch.fnmatch(rel_path_str, normalized_pattern) or fnmatch.fnmatch(
            filename, normalized_pattern
        )

    async def _search_file_content(
        self, file_path: pathlib.Path, rel_path_str: str, search_text: str
    ) -> int:
        """Search file for search_text; yield matches via send_match. Returns match count. May raise CancelledError."""
        if file_path.stat().st_size > MAX_READABLE_FILE_SIZE:
            return 0
        match_count = 0
        async with aiofiles.open(
            file_path, "r", encoding="utf-8", errors="ignore"
        ) as f:
            line_num = 0
            while True:
                line = await f.readline()
                if not line:
                    break
                line_num += 1
                if self.stop_event.is_set():
                    raise asyncio.CancelledError
                if search_text in line:
                    self.send_match(rel_path_str, line_num, line.strip(), search_text)
                    match_count += 1
        return match_count

    async def _search_one_file(
        self,
        file_path: pathlib.Path,
        root_path: pathlib.Path,
        filename: str,
        normalized_pattern: str,
        search_text: str,
        files_searched: int,
    ) -> tuple[int, bool]:
        """Search one file; sends 'scanning' then searches. Returns (match_count, True if searched)."""
        try:
            rel_path = file_path.relative_to(root_path)
            rel_path_str = str(rel_path).replace("\\", "/")
        except (ValueError, OSError):
            return 0, False
        if not self._file_matches_pattern(rel_path_str, filename, normalized_pattern):
            return 0, False
        try:
            self.write_message(
                json.dumps(
                    {
                        "type": "scanning",
                        "file_path": rel_path_str,
                        "files_searched": files_searched,
                    }
                )
            )
        except (tornado.websocket.WebSocketClosedError, RuntimeError):
            pass
        try:
            count = await self._search_file_content(
                file_path, rel_path_str, search_text
            )
            return count, True
        except (UnicodeDecodeError, OSError):
            return 0, True
        except Exception as other_err:
            logging.debug("Error searching file %s: %s", file_path, other_err)
            return 0, True

    async def _run_search_walk(
        self,
        root_path: pathlib.Path,
        normalized_pattern: str,
        search_text: str,
    ) -> tuple[int, int] | None:
        """Walk directory tree and search files. Returns (matches, files_searched) or None if auth expired."""
        matches = 0
        files_searched = 0
        for dirpath, _dirnames, filenames in os.walk(root_path):
            if self.stop_event.is_set():
                raise asyncio.CancelledError
            for filename in filenames:
                if self.stop_event.is_set():
                    raise asyncio.CancelledError
                file_path = pathlib.Path(dirpath) / filename
                match_delta, counted = await self._search_one_file(
                    file_path,
                    root_path,
                    filename,
                    normalized_pattern,
                    search_text,
                    files_searched + 1,
                )
                matches += match_delta
                if counted:
                    files_searched += 1
            if files_searched % 20 == 0 and not await self._yield_and_check_auth():
                return None
        return matches, files_searched

    async def _yield_and_check_auth(self) -> bool:
        """Yield control and check auth; return False if session expired (caller should abort)."""
        await asyncio.sleep(0)
        if self.get_current_user():
            return True
        logging.warning("SuperSearchWebSocket: Authentication expired during search")
        self._send_auth_required_and_close(
            "Your session expired during search. Please log in again.",
            REDIRECT_SEARCH,
            AUTH_EXPIRED,
        )
        return False

    def _send_search_completion(self, matches: int, files_searched: int):
        """Send search_complete or no_files message."""
        if matches == 0:
            self.write_message(
                json.dumps(
                    {
                        "type": "no_files",
                        "message": f"No matches found in {files_searched} files.",
                        "files_searched": files_searched,
                    }
                )
            )
        else:
            self.write_message(
                json.dumps(
                    {
                        "type": "search_complete",
                        "matches": matches,
                        "files_searched": files_searched,
                    }
                )
            )

    async def perform_search(self, pattern: str, search_text: str):
        """Perform the super search and stream results"""
        # Validate authentication at the start of search
        user = self.get_current_user()
        if not user:
            logging.warning(
                "SuperSearchWebSocket: Authentication failed at search start"
            )
            self._send_auth_required_and_close(
                AUTH_EXPIRED, REDIRECT_SEARCH, AUTH_EXPIRED
            )
            return

        try:
            # Send search start notification
            self.write_message(
                json.dumps(
                    {
                        "type": "search_start",
                        "pattern": pattern,
                        "search_text": search_text,
                    }
                )
            )

            # Normalize pattern to use forward slashes for matching
            normalized_pattern = pattern.replace("\\", "/")
            root_path = pathlib.Path(ROOT_DIR).resolve()
            result = await self._run_search_walk(
                root_path, normalized_pattern, search_text
            )
            if result is None:
                return
            matches, files_searched = result
            self._send_search_completion(matches, files_searched)

        except asyncio.CancelledError:
            self.write_message(json.dumps({"type": "cancelled"}))
            raise
        except Exception as e:
            logging.error(f"Super search error: {e}", exc_info=True)
            self.write_message(
                json.dumps({"type": "error", "message": f"Search failed: {str(e)}"})
            )

    def send_match(
        self, file_path: str, line_number: int, line_content: str, search_text: str
    ):
        message = {
            "type": "match",
            "file_path": file_path,
            "line_number": line_number,
            "line_content": line_content,
            "search_text": search_text,
        }
        try:
            self.write_message(json.dumps(message))
        except (tornado.websocket.WebSocketClosedError, RuntimeError):
            pass

    def on_close(self):
        if self.search_task:
            self.stop_event.set()
        super().on_close()

    def check_origin(self, origin):
        return is_valid_websocket_origin(self, origin)


class UserSearchAPIHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        """Search users by username (for share access control)"""

        query = self.get_argument("q", "").strip()
        if len(query) < 1:
            self.write({"users": []})
            return

        try:
            users = search_users(self.db_conn, query)
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
            self.write({"error": FILESHARE_DISABLED_MSG})
            return

        file_path = self.get_argument("path", "").strip()
        if not file_path:
            self.set_status(400)
            self.write({"error": "File path is required"})
            return

        try:
            # Find shares that contain this file
            db_conn = self.db_conn
            if not db_conn:
                self.set_status(500)
                self.write({"error": DB_NOT_AVAILABLE_MSG})
                return
            matching_shares = get_shares_for_path(db_conn, file_path)

            # Format response
            formatted_shares = []
            for share in matching_shares:
                allowed_users = share.get("allowed_users")
                share_info = {
                    "id": share["id"],
                    "created": share.get("created", ""),
                    "allowed_users": allowed_users if allowed_users is not None else [],
                    "url": f"/shared/{share['id']}",
                    "paths": share.get("paths", []),
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
            self.write({"error": FILESHARE_DISABLED_MSG})
            return

        share_id = self.get_argument("id", "").strip()
        if not share_id:
            self.set_status(400)
            self.write({"error": "Share ID is required"})
            return

        try:
            db_conn = self.db_conn
            if not db_conn:
                self.set_status(500)
                self.write({"error": DB_NOT_AVAILABLE_MSG})
                return
            share = get_share_by_id(db_conn, share_id)
            if not share:
                self.set_status(404)
                self.write({"error": "Share not found"})
                return

            allowed_users = share.get("allowed_users")
            share_info = {
                "id": share["id"],
                "created": share.get("created", ""),
                "allowed_users": allowed_users if allowed_users is not None else [],
                "url": f"/shared/{share['id']}",
                "paths": share.get("paths", []),
                "secret_token": share.get("secret_token"),
                "share_type": share.get("share_type", "static"),
                "allow_list": share.get("allow_list", []),
                "avoid_list": share.get("avoid_list", []),
                "expiry_date": share.get("expiry_date"),
            }

            self.write({"share": share_info})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})


class ShareListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not is_feature_enabled("file_share", True):
            self.set_status(403)
            self.write({"error": FILESHARE_DISABLED_MSG})
            return

        db_conn = self.db_conn
        if not db_conn:
            self.set_status(500)
            self.write({"error": DB_NOT_AVAILABLE_MSG})
            return
        shares = get_all_shares(db_conn)
        self.write({"shares": shares})


class WebSocketStatsHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin()
    def get(self):
        """Return WebSocket connection statistics"""

        stats = {
            "feature_flags": FeatureFlagSocketHandler.connection_manager.get_stats(),
            "file_streaming": FileStreamHandler.connection_manager.get_stats(),
            "super_search": SuperSearchWebSocketHandler.connection_manager.get_stats(),
            "timestamp": time.time(),
        }

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(stats, indent=2))
