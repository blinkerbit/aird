import base64
import fnmatch
import json
import logging
import mimetypes
import os
import pathlib
import re
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
    get_username_string_for_db,
    get_user_root,
    require_db,
)

from aird.utils.util import (
    get_files_in_directory,
    is_video_file,
    is_audio_file,
    WebSocketConnectionManager,
    get_current_feature_flags,
    augment_with_shared_status,
)
from aird.core.security import (
    is_within_root,
    is_valid_websocket_origin,
)
from aird.core.filter_expression import FilterExpression
from aird.config import (
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

        user_root = get_user_root(self)
        self.file_path = os.path.abspath(os.path.join(user_root, unquote(path)))
        if not is_within_root(self.file_path, user_root) or not os.path.isfile(
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
        self._stream_task = asyncio.create_task(self.stream_file())

    async def _handle_stream_file_action(self, data: dict) -> None:
        """Handle the stream_file action."""
        rel_path = (data.get("file_path") or "").strip()
        if not rel_path:
            self.write_message(
                json.dumps({"type": "error", "message": "file_path is required"})
            )
            return
        user_root = get_user_root(self)
        abs_path = os.path.abspath(os.path.join(user_root, rel_path))
        if not is_within_root(abs_path, user_root):
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
            self._stream_task = asyncio.create_task(self.stream_file())
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
                asyncio.create_task(self.file.close())
        except Exception as close_err:
            logging.debug("Error closing file stream: %s", close_err)
        super().on_close()

    def check_origin(self, origin):
        return is_valid_websocket_origin(self, origin)


class FileListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, path):
        user_root = get_user_root(self)
        abspath = os.path.abspath(os.path.join(user_root, path))
        if not is_within_root(abspath, user_root):
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
                augment_with_shared_status(
                    files, path, self.get_service("share_service").list_shares(db_conn)
                )

            result = {
                "path": path,
                "files": files,
                "is_video": is_video_file(path),
                "is_audio": is_audio_file(path),
            }
            self.write(result)
        except Exception as e:
            logging.error("Error listing files: %s", e, exc_info=True)
            self.set_status(500)
            self.write("Internal server error")


class SuperSearchHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not self.require_feature(
            "super_search",
            True,
            body="Feature disabled: Super Search is currently disabled by administrator",
        ):
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

        search_mode = data.get("search_mode", "content")

        if self.search_task and not self.search_task.done():
            self.stop_event.set()
            self._cancel_task = asyncio.create_task(
                self._await_cancellation_and_start_new(
                    (pattern, search_text, search_mode)
                )
            )
        else:
            self.stop_event.clear()
            self.search_task = asyncio.create_task(
                self.perform_search(pattern, search_text, search_mode)
            )

    async def _await_cancellation_and_start_new(self, args):
        try:
            await self.search_task
        except asyncio.CancelledError:
            self.stop_event.clear()
            pattern, search_text, search_mode = args
            self.search_task = asyncio.create_task(
                self.perform_search(pattern, search_text, search_mode)
            )
            raise
        self.stop_event.clear()
        pattern, search_text, search_mode = args
        self.search_task = asyncio.create_task(
            self.perform_search(pattern, search_text, search_mode)
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
        search_mode: str = "content",
    ) -> tuple[int, bool]:
        """Search one file; sends 'scanning' then searches. Returns (match_count, True if searched)."""
        try:
            rel_path = file_path.relative_to(root_path)
            rel_path_str = str(rel_path).replace("\\", "/")
        except (ValueError, OSError):
            return 0, False
        if not self._file_matches_pattern(rel_path_str, filename, normalized_pattern):
            return 0, False

        if search_mode == "filename":
            if self.stop_event.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(0)  # yield so on_close / stop_event can be processed

            search_lower = search_text.lower()
            filename_lower = filename.lower()
            rel_path_lower = rel_path_str.lower()

            if any(c in search_text for c in "*?[]"):
                # Use fnmatchcase to ensure consistent case-insensitive behavior across platforms
                # since we manually lowercased the strings.
                match = fnmatch.fnmatchcase(
                    filename_lower, search_lower
                ) or fnmatch.fnmatchcase(rel_path_lower, search_lower)
            else:
                match = search_lower in filename_lower or search_lower in rel_path_lower

            if match:
                self.send_match(rel_path_str, 0, rel_path_str, search_text)
                return 1, True
            return 0, True

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
        search_mode: str = "content",
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
                    search_mode,
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

    async def perform_search(
        self, pattern: str, search_text: str, search_mode: str = "content"
    ):
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
                        "search_mode": search_mode,
                    }
                )
            )

            # Normalize pattern to use forward slashes for matching
            normalized_pattern = pattern.replace("\\", "/")
            root_path = pathlib.Path(get_user_root(self)).resolve()
            result = await self._run_search_walk(
                root_path, normalized_pattern, search_text, search_mode
            )
            if result is None:
                return
            matches, files_searched = result
            self._send_search_completion(matches, files_searched)

        except asyncio.CancelledError:
            try:
                self.write_message(json.dumps({"type": "cancelled"}))
            except (tornado.websocket.WebSocketClosedError, RuntimeError):
                pass
            raise
        except Exception as e:
            logging.error(f"Super search error: {e}", exc_info=True)
            try:
                self.write_message(
                    json.dumps({"type": "error", "message": f"Search failed: {str(e)}"})
                )
            except (tornado.websocket.WebSocketClosedError, RuntimeError):
                pass

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

        def action():
            users = self.get_service("user_service").search_users(self.db_conn, query)
            return {"users": users}

        self.run_json_action(action, on_error_message="Search failed")


class ShareDetailsAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Get share details for a specific file"""
        if not self.require_feature(
            "file_share", True, body={"error": FILESHARE_DISABLED_MSG}
        ):
            return

        file_path = self.get_argument("path", "").strip()
        if not file_path:
            self.write_json_error(400, "File path is required")
            return

        def action():
            # Find shares that contain this file
            db_conn = self.require_db_connection(DB_NOT_AVAILABLE_MSG)
            if not db_conn:
                return None
            share_service = self.get_service("share_service")
            if share_service is not None:
                matching_shares = share_service.get_shares_for_path(db_conn, file_path)
            else:
                matching_shares = self.get_service("share_service").get_shares_for_path(
                    db_conn, file_path
                )

            # Format response
            formatted_shares = []
            for share in matching_shares:
                allowed_users = share.get("allowed_users")
                modify_users = share.get("modify_users")
                share_info = {
                    "id": share["id"],
                    "created": share.get("created", ""),
                    "allowed_users": allowed_users if allowed_users is not None else [],
                    "modify_users": modify_users if modify_users is not None else [],
                    "url": f"/shared/{share['id']}",
                    "paths": share.get("paths", []),
                }
                formatted_shares.append(share_info)

            return {"shares": formatted_shares}

        self.run_json_action(
            action, on_error_message="Failed to retrieve share details"
        )


class ShareDetailsByIdAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Get share details for a specific share ID"""
        if not self.require_feature(
            "file_share", True, body={"error": FILESHARE_DISABLED_MSG}
        ):
            return

        share_id = self.get_argument("id", "").strip()
        if not share_id:
            self.write_json_error(400, "Share ID is required")
            return

        def action():
            db_conn = self.require_db_connection(DB_NOT_AVAILABLE_MSG)
            if not db_conn:
                return None
            share_service = self.get_service("share_service")
            if share_service is not None:
                share = share_service.get_share(db_conn, share_id)
            else:
                share = self.get_service("share_service").get_share(db_conn, share_id)
            if not share:
                self.write_json_error(404, "Share not found")
                return None

            allowed_users = share.get("allowed_users")
            modify_users = share.get("modify_users")
            share_info = {
                "id": share["id"],
                "created": share.get("created", ""),
                "allowed_users": allowed_users if allowed_users is not None else [],
                "modify_users": modify_users if modify_users is not None else [],
                "url": f"/shared/{share['id']}",
                "paths": share.get("paths", []),
                "has_token": share.get("secret_token") is not None,
                "share_type": share.get("share_type", "static"),
                "allow_list": share.get("allow_list", []),
                "avoid_list": share.get("avoid_list", []),
                "expiry_date": share.get("expiry_date"),
                "download_count": self.get_service("share_service").get_download_count(
                    db_conn, share_id
                ),
            }

            return {"share": share_info}

        self.run_json_action(
            action, on_error_message="Failed to retrieve share details"
        )


class ShareListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not self.require_feature(
            "file_share", True, body={"error": FILESHARE_DISABLED_MSG}
        ):
            return

        db_conn = self.require_db_connection(DB_NOT_AVAILABLE_MSG)
        if not db_conn:
            return
        share_service = self.get_service("share_service")
        if share_service is not None:
            shares = share_service.list_shares(db_conn)
        else:
            shares = self.get_service("share_service").list_shares(db_conn)
        self.write({"shares": shares})


class FavoriteToggleAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not self.require_feature(
            "favorites", True, body={"error": "Favorites disabled"}
        ):
            return
        db_conn = self.require_db_connection(DB_NOT_AVAILABLE_MSG)
        if not db_conn:
            return

        def action():
            try:
                body = json.loads(self.request.body)
            except Exception:
                self.write_json_error(400, "Invalid JSON")
                return None
            path = body.get("path", "").strip()
            if not path:
                self.write_json_error(400, "path is required")
                return None
            username = get_username_string_for_db(self)
            if not username:
                self.write_json_error(401, "Could not resolve username")
                return None
            is_fav = self.get_service("favorites_service").toggle(
                db_conn, username, path
            )
            return {"favorited": is_fav, "path": path}

        self.run_json_action(action, on_error_message="Failed to toggle favorite")


class FavoritesListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not self.require_feature(
            "favorites", True, body={"error": "Favorites disabled"}
        ):
            return
        db_conn = self.require_db_connection(DB_NOT_AVAILABLE_MSG)
        if not db_conn:
            return
        username = get_username_string_for_db(self)
        if not username:
            self.write_json_error(401, "Could not resolve username")
            return
        favorites = self.get_service("favorites_service").get_favorites(
            db_conn, username
        )
        self.write({"favorites": favorites})
