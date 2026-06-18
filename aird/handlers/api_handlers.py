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
    login_matches_share_creator_field,
    require_action,
    require_db,
)
from aird.constants.input_limits import (
    API_LAST_N_MAX,
    InputTooLongError,
    REL_PATH_MAX_LEN,
    SHARE_ID_MAX_LEN,
    USER_SEARCH_QUERY_MAX_LEN,
)
from aird.core.input_validation import validate_super_search_glob, validate_ws_search

from aird.utils.util import (
    get_files_in_directory,
    is_video_file,
    is_audio_file,
    WebSocketConnectionManager,
    get_current_feature_flags,
    augment_with_shared_status,
    share_relevant_for_viewers_file_tree,
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
    """Legacy WebSocket kept for backward compatibility; new clients use GET /api/features."""

    connection_manager = WebSocketConnectionManager(
        "feature_flags", default_max_connections=50, default_idle_timeout=600
    )

    def open(self):
        if not self.get_current_user():
            self.close(code=1008, reason=AUTH_REQUIRED)
            return

        if not self.register_connection():
            return

        current_flags = self._get_current_feature_flags()
        self.write_message(json.dumps(current_flags))

    def check_origin(self, origin):
        return is_valid_websocket_origin(self, origin)

    def _get_current_feature_flags(self):
        return get_current_feature_flags()

    @classmethod
    def send_updates(cls):
        current_flags = get_current_feature_flags()
        cls.connection_manager.broadcast_message(json.dumps(current_flags))


class FeatureFlagAPIHandler(BaseHandler):
    """GET /api/features — lightweight JSON endpoint for on-demand flag checks."""

    @tornado.web.authenticated
    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(get_current_feature_flags()))


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
        n_lines = min(n_lines, API_LAST_N_MAX)

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
            logging.exception("Error reading file: %s", self.file_path)
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
        if self.reject_oversized_ws_message(message):
            return
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
                self._close_task = asyncio.create_task(self.file.close())
        except Exception as close_err:
            logging.debug("Error closing file stream: %s", close_err)
        super().on_close()

    def check_origin(self, origin):
        return is_valid_websocket_origin(self, origin)


class FolderSizeAPIHandler(BaseHandler):
    """GET /api/folder-size?path= — recursive folder byte total (on demand)."""

    @tornado.web.authenticated
    async def get(self):
        from aird.core.folder_size import (
            compute_folder_size,
            norm_rel_path,
            resolve_folder_abspath,
        )
        from aird.utils.util import format_size

        path = self.get_argument("path", "").strip()
        norm_path = norm_rel_path(path)
        decision = self.check_access("file.list", resource_path=norm_path)
        if decision is not None and decision.is_deny:
            self.set_status(403)
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"error": decision.reason or "Access denied"}))
            return

        user_root = get_user_root(self)
        abs_path = resolve_folder_abspath(user_root, path)
        if not abs_path:
            self.set_status(404)
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"error": "Folder not found"}))
            return

        try:
            total_bytes, file_count = await asyncio.to_thread(
                compute_folder_size, abs_path
            )
        except Exception:
            logging.exception("Folder size calculation failed for %s", norm_path)
            self.set_status(500)
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"error": "Folder size calculation failed"}))
            return

        self.set_header("Content-Type", "application/json")
        self.write(
            json.dumps(
                {
                    "path": norm_path,
                    "bytes": total_bytes,
                    "files": file_count,
                    "size_str": format_size(total_bytes),
                }
            )
        )


class FileListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("file.list", resource_arg="path")
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
                    files,
                    path,
                    self.get_service("share_service").list_shares(db_conn),
                    db_conn=db_conn,
                    root_dir=user_root,
                    viewer_username=get_username_string_for_db(self),
                    viewer_is_admin=self.is_admin_user(),
                )

            result = {
                "path": path,
                "files": files,
                "is_video": is_video_file(path),
                "is_audio": is_audio_file(path),
            }
            self.write(result)
        except Exception:
            logging.exception("Error listing files")
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
    # Periodic walk progress while many files skip the user's glob — avoids a “hung” UI
    _walk_scan_interval = 40

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
        if self.reject_oversized_ws_message(message):
            return
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
        try:
            pattern, search_text = validate_ws_search(pattern, search_text)
        except InputTooLongError:
            self.write_message(
                json.dumps({"type": "error", "message": "Search parameters too long"})
            )
            return
        glob_err = validate_super_search_glob(pattern)
        if glob_err:
            self.write_message(json.dumps({"type": "error", "message": glob_err}))
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

    def _emit_scanning(self, rel_path_str: str, files_searched: int) -> None:
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

    async def _emit_walk_tick(self, rel_path_str: str, files_visited: int) -> None:
        """Liveness ticker for files skipped by the glob (no duplicate with per-match scanning)."""
        self._emit_scanning(rel_path_str, files_visited)
        await asyncio.sleep(0)

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

    def _match_filename_search(self, rel_path_str: str, filename: str, search_text: str) -> bool:
        """Return True if filename/path matches search_text."""
        search_lower = search_text.lower()
        filename_lower = filename.lower()
        rel_path_lower = rel_path_str.lower()
        if any(c in search_text for c in "*?[]"):
            return (
                fnmatch.fnmatchcase(filename_lower, search_lower)
                or fnmatch.fnmatchcase(rel_path_lower, search_lower)
            )
        return search_lower in filename_lower or search_lower in rel_path_lower

    async def _search_one_file(
        self,
        file_path: pathlib.Path,
        root_path: pathlib.Path,
        filename: str,
        normalized_pattern: str,
        search_text: str,
        files_searched: int,
        search_mode: str = "content",
        *,
        rel_path_str: str | None = None,
    ) -> tuple[int, bool]:
        """Search one file; sends 'scanning' after glob match; returns (match_count, True if searched)."""
        if rel_path_str is None:
            try:
                rel_path = file_path.relative_to(root_path)
                rel_path_str = str(rel_path).replace("\\", "/")
            except (ValueError, OSError):
                return 0, False
        if not self._file_matches_pattern(rel_path_str, filename, normalized_pattern):
            return 0, False

        self._emit_scanning(rel_path_str, files_searched)

        if search_mode == "filename":
            if self.stop_event.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(0)
            if self._match_filename_search(rel_path_str, filename, search_text):
                self.send_match(rel_path_str, 0, rel_path_str, search_text)
                return 1, True
            return 0, True

        try:
            count = await self._search_file_content(file_path, rel_path_str, search_text)
            return count, True
        except (UnicodeDecodeError, OSError):
            return 0, True
        except Exception as other_err:
            logging.debug("Error searching file %s: %s", file_path, other_err)
            return 0, True

    async def _process_walk_file(
        self, file_path, root_path, filename, normalized_pattern,
        search_text, files_searched, search_mode, files_visited,
    ) -> tuple[int, bool]:
        """Process one file during walk. Returns (match_delta, counted)."""
        try:
            rel_path_str = str(file_path.relative_to(root_path)).replace("\\", "/")
        except ValueError:
            return 0, False
        glob_match = self._file_matches_pattern(rel_path_str, filename, normalized_pattern)
        if not glob_match:
            if files_visited == 1 or files_visited % SuperSearchWebSocketHandler._walk_scan_interval == 0:
                await self._emit_walk_tick(rel_path_str, files_visited)
            return 0, False
        return await self._search_one_file(
            file_path, root_path, filename, normalized_pattern,
            search_text, files_searched + 1, search_mode, rel_path_str=rel_path_str,
        )

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
        files_visited = 0
        for dirpath, _dirnames, filenames in os.walk(root_path, followlinks=False):
            if self.stop_event.is_set():
                raise asyncio.CancelledError
            for filename in filenames:
                if self.stop_event.is_set():
                    raise asyncio.CancelledError
                file_path = pathlib.Path(dirpath) / filename
                files_visited += 1
                match_delta, counted = await self._process_walk_file(
                    file_path, root_path, filename, normalized_pattern,
                    search_text, files_searched, search_mode, files_visited,
                )
                matches += match_delta
                if counted:
                    files_searched += 1
            if files_visited > 0 and files_visited % 20 == 0 and not await self._yield_and_check_auth():
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
            logging.exception("Super search error")
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
        if len(query) > USER_SEARCH_QUERY_MAX_LEN:
            self.write({"users": []})
            return

        def action():
            users = self.get_service("user_service").search_users(self.db_conn, query)
            return {"users": users}

        self.run_json_action(action, on_error_message="Search failed")


def _redact_share_secret_token(share: dict) -> dict:
    """Copy share dict suitable for non-owner API consumers (no raw token)."""
    out = dict(share)
    out["secret_token"] = None
    return out


def _share_eligible_for_path_details(handler, share: dict) -> bool:
    """Path-based share popup only for shares on the viewer's tree or explicit cross-home access."""
    return share_relevant_for_viewers_file_tree(
        share,
        viewer_username=get_username_string_for_db(handler),
        viewer_is_admin=handler.is_admin_user(),
        viewer_root=get_user_root(handler),
    )


def _format_share_for_path_details(handler, share: dict) -> dict | None:
    """Return formatted share info dict, or None if not eligible."""
    if not _share_eligible_for_path_details(handler, share):
        return None
    allowed_users = share.get("allowed_users")
    modify_users = share.get("modify_users")
    show_secret = handler.can_manage_share_secrets(share)
    token = share.get("secret_token")
    return {
        "id": share["id"],
        "created": share.get("created", ""),
        "allowed_users": allowed_users if allowed_users is not None else [],
        "modify_users": modify_users if modify_users is not None else [],
        "url": f"/shared/{share['id']}",
        "paths": share.get("paths", []),
        "share_type": share.get("share_type", "static"),
        "tag_name": share.get("tag_name"),
        "has_token": token is not None,
        "secret_token": token if show_secret else None,
    }


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
        if len(file_path) > REL_PATH_MAX_LEN:
            self.write_json_error(400, "File path is too long")
            return

        def action():
            db_conn = self.require_db_connection(DB_NOT_AVAILABLE_MSG)
            if not db_conn:
                return None
            user_root = get_user_root(self)
            share_service = self.get_service("share_service")
            matching_shares = share_service.get_shares_for_path(db_conn, file_path, user_root)
            formatted_shares = [
                info for share in matching_shares
                if (info := _format_share_for_path_details(self, share)) is not None
            ]
            return {"shares": formatted_shares}

        self.run_json_action(action, on_error_message="Failed to retrieve share details")


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
        if len(share_id) > SHARE_ID_MAX_LEN:
            self.write_json_error(400, "Invalid share id")
            return

        def action():
            db_conn = self.require_db_connection(DB_NOT_AVAILABLE_MSG)
            if not db_conn:
                return None
            
            share_service = self.get_service("share_service")
            if share_service is None:
                self.write_json_error(500, "Share service not available")
                return None
                
            share = share_service.get_share(db_conn, share_id)
            if not share:
                self.write_json_error(404, "Share not found")
                return None

            if not self.can_edit_share_paths(share):
                self.write_json_error(403, "Access denied")
                return None

            allowed_users = share.get("allowed_users")
            modify_users = share.get("modify_users")
            show_secret = self.can_manage_share_secrets(share)
            token = share.get("secret_token")
            share_info = {
                "id": share["id"],
                "created": share.get("created", ""),
                "allowed_users": allowed_users if allowed_users is not None else [],
                "modify_users": modify_users if modify_users is not None else [],
                "url": f"/shared/{share['id']}",
                "paths": share.get("paths", []),
                "has_token": token is not None,
                "secret_token": token if show_secret else None,
                "share_type": share.get("share_type", "static"),
                "tag_name": share.get("tag_name"),
                "allow_list": share.get("allow_list", []),
                "avoid_list": share.get("avoid_list", []),
                "expiry_date": share.get("expiry_date"),
                "download_count": share_service.get_download_count(
                    db_conn, share_id
                ),
                "is_owner": self.is_share_owner(share),
                "can_revoke": self.is_share_owner(share),
                "can_manage": self.is_share_owner(share),
                "can_edit_paths": self.can_edit_share_paths(share),
            }

            return {"share": share_info}

        self.run_json_action(
            action, on_error_message="Failed to retrieve share details"
        )


def _classify_share_for_user(
    share: dict,
    current_user: str,
    is_admin: bool,
) -> tuple[bool, bool]:
    """Return (is_my_share, is_shared_with_me) for a given user."""
    creator = (share.get("created_by") or "").strip()
    allowed_raw = share.get("allowed_users")
    allowed_list = allowed_raw if isinstance(allowed_raw, list) else []
    mod_list = share.get("modify_users") or []

    if creator and current_user and login_matches_share_creator_field(
        creator, current_user
    ):
        return True, False
    if not creator and is_admin:
        return True, False
    if current_user and current_user in mod_list:
        return False, True
    if not creator and current_user and (
        allowed_raw is None
        or (isinstance(allowed_raw, list) and current_user in allowed_list)
    ):
        return False, True
    if current_user and isinstance(allowed_raw, list) and current_user in allowed_list:
        return False, True
    return False, False


def _attach_share_capabilities(handler: BaseHandler, share: dict) -> dict:
    """Add ownership / editor flags for the share management UI."""
    out = dict(share)
    out["is_owner"] = handler.is_share_owner(share)
    out["can_revoke"] = handler.is_share_owner(share)
    out["can_manage"] = handler.is_share_owner(share)
    out["can_edit_paths"] = handler.can_edit_share_paths(share)
    return out


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
        all_shares = (
            share_service.list_shares(db_conn) if share_service is not None else {}
        )

        current_user = get_username_string_for_db(self) or ""
        is_admin = self.is_admin_user()

        my_shares = {}
        shared_with_me = []
        for sid, share in all_shares.items():
            is_mine, is_shared = _classify_share_for_user(share, current_user, is_admin)
            if is_mine:
                my_shares[sid] = _attach_share_capabilities(self, share)
            elif is_shared:
                redacted = _redact_share_secret_token(share)
                shared_with_me.append(_attach_share_capabilities(self, redacted))

        self.write({"shares": my_shares, "shared_with_me": shared_with_me})


class FavoriteToggleAPIHandler(BaseHandler):
    @tornado.web.authenticated
    @require_action("favorites.toggle")
    def post(self):
        if not self.require_feature(
            "favorites", True, body={"error": "Favorites disabled"}
        ):
            return
        db_conn = self.require_db_connection(DB_NOT_AVAILABLE_MSG)
        if not db_conn:
            return

        def action():
            body = self.parse_json_body() or {}
            path = body.get("path", "").strip()
            if not path:
                self.write_json_error(400, "path is required")
                return None
            if len(path) > REL_PATH_MAX_LEN:
                self.write_json_error(400, "path is too long")
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
