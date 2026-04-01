import asyncio
import json
import pathlib

import pytest
import tornado.websocket
from unittest.mock import AsyncMock, MagicMock, patch

from aird.handlers.api_handlers import (
    DB_NOT_AVAILABLE_MSG,
    FavoriteToggleAPIHandler,
    FavoritesListAPIHandler,
    FeatureFlagSocketHandler,
    FileListAPIHandler,
    FileStreamHandler,
    ShareDetailsAPIHandler,
    ShareDetailsByIdAPIHandler,
    ShareListAPIHandler,
    SuperSearchHandler,
    SuperSearchWebSocketHandler,
    UserSearchAPIHandler,
)
from aird.handlers.constants import AUTH_REQUIRED
from aird.config import MAX_READABLE_FILE_SIZE
from tests.handler_helpers import _default_services, authenticate, patch_db_conn, prepare_handler


class _MockAioLinesCM:
    """Async context manager yielding an async-iterable file for aiofiles.open patches."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def __aenter__(self):
        return _AsyncLineIter(self._lines)

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _AsyncLineIter:
    def __init__(self, lines):
        self._it = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration as e:
            raise StopAsyncIteration from e


def make_request_handler(handler_cls):
    """Helper for BaseHandler descendants."""
    app = MagicMock()
    app.settings = {"cookie_secret": "test_secret", "services": _default_services()}
    request = MagicMock()
    request.protocol = "http"
    handler = prepare_handler(handler_cls(app, request))
    authenticate(handler, role="admin")
    return handler


def make_ws_handler(handler_cls):
    app = MagicMock()
    app.settings = {"cookie_secret": "test_secret", "services": _default_services()}
    request = MagicMock()
    request.headers = {}
    request.path = "/ws"
    request.connection = MagicMock()
    handler = handler_cls(app, request)
    handler.write_message = MagicMock()
    handler.close = MagicMock()
    handler.request = request
    return handler


def make_file_stream_ws_handler():
    """FileStreamHandler uses ``await self.write_message(...)``; needs AsyncMock."""
    handler = make_ws_handler(FileStreamHandler)
    handler.write_message = AsyncMock()
    return handler


async def _cancel_stream_task(handler):
    """Stop background ``stream_file`` task created by ``FileStreamHandler.open``."""
    handler.is_streaming = False
    handler.stop_event.set()
    task = getattr(handler, "_stream_task", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestFileListAPIHandler:
    def test_get_success(self):
        handler = make_request_handler(FileListAPIHandler)
        with patch("os.path.abspath", return_value="/root/path"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isdir", return_value=True), patch(
            "aird.handlers.api_handlers.get_files_in_directory",
            return_value=[{"name": "file.txt"}],
        ), patch(
            "aird.handlers.api_handlers.is_video_file", return_value=False
        ), patch(
            "aird.handlers.api_handlers.is_audio_file", return_value=False
        ), patch_db_conn(
            MagicMock(), modules=["aird.handlers.api_handlers"]
        ), patch(
            "aird.services.share_service.get_all_shares",
            return_value={"share": {"paths": ["path/file.txt"]}},
        ):

            handler.get("path")
            handler.write.assert_called()
            payload = handler.write.call_args[0][0]
            assert payload["files"][0]["name"] == "file.txt"

    def test_get_forbidden_path(self):
        handler = make_request_handler(FileListAPIHandler)
        handler.set_status = MagicMock()
        with patch("os.path.abspath", return_value="/bad"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=False
        ):
            handler.get("bad")
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with("Access denied")

    def test_missing_directory(self):
        handler = make_request_handler(FileListAPIHandler)
        handler.set_status = MagicMock()
        with patch("os.path.abspath", return_value="/root/path"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isdir", return_value=False):
            handler.get("missing")
            handler.set_status.assert_called_with(404)
            handler.write.assert_called_with("Directory not found")

    def test_get_handles_exception(self):
        handler = make_request_handler(FileListAPIHandler)
        handler.set_status = MagicMock()
        with patch("os.path.abspath", return_value="/root/path"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isdir", return_value=True), patch(
            "aird.handlers.api_handlers.get_files_in_directory",
            side_effect=RuntimeError("boom"),
        ):
            handler.get("path")
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with("Internal server error")


class TestSuperSearchHandler:
    def test_get_renders_when_enabled(self):
        handler = make_request_handler(SuperSearchHandler)
        handler.get_argument = MagicMock(return_value="//nested//")
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=True):
            handler.get()
            handler.render.assert_called()
            assert handler.render.call_args[1]["current_path"] == "nested"

    def test_get_feature_disabled(self):
        handler = make_request_handler(SuperSearchHandler)
        handler.set_status = MagicMock()
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=False):
            handler.get()
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with(
                "Feature disabled: Super Search is currently disabled by administrator"
            )


class TestSuperSearchWebSocketHandler:
    def test_open_requires_auth(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value=None)
        handler.open()
        handler.write_message.assert_called()
        handler.close.assert_called_with(code=1008, reason="Authentication required")

    def test_open_connection_limit(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={"username": "admin"})
        with patch.object(
            SuperSearchWebSocketHandler.connection_manager,
            "add_connection",
            return_value=False,
        ):
            handler.open()
            handler.close.assert_called_with(
                code=1013, reason="Connection limit exceeded"
            )

    @pytest.mark.asyncio
    async def test_on_message_auth_failure(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value=None)
        await handler.on_message(json.dumps({"pattern": "*.py", "search_text": "foo"}))
        handler.close.assert_called_with(
            code=1008, reason="Your session has expired. Please log in again."
        )


class TestUserSearchAPIHandler:
    def test_requires_db_connection(self):
        handler = make_request_handler(UserSearchAPIHandler)
        handler.set_status = MagicMock()
        handler.get_argument = MagicMock(return_value="bob")
        with patch_db_conn(None, modules=["aird.handlers.api_handlers"]):
            handler.get()
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with(
                {"error": "Database connection not available"}
            )

    def test_returns_empty_for_short_query(self):
        handler = make_request_handler(UserSearchAPIHandler)
        handler.get_argument = MagicMock(return_value=" ")
        with patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]):
            handler.get()
            handler.write.assert_called_with({"users": []})

    def test_handles_search_error(self):
        handler = make_request_handler(UserSearchAPIHandler)
        handler.set_status = MagicMock()
        handler.get_argument = MagicMock(return_value="bob")
        with patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch(
            "aird.services.user_service.search_users", side_effect=RuntimeError("nope")
        ):
            handler.get()
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with({"error": "Search failed"})

    def test_search_success(self):
        handler = make_request_handler(UserSearchAPIHandler)
        handler.get_argument = MagicMock(return_value="alice")
        with patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch(
            "aird.services.user_service.search_users",
            return_value=[{"username": "alice"}],
        ):
            handler.get()
            handler.write.assert_called_with({"users": [{"username": "alice"}]})


class TestShareDetailsAPIHandler:
    def _make_handler(self):
        handler = make_request_handler(ShareDetailsAPIHandler)
        handler.get_argument = MagicMock(return_value="file.txt")
        return handler

    def test_feature_disabled(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=False):
            handler.get()
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with({"error": "File sharing is disabled"})

    def test_missing_path(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        handler.get_argument = MagicMock(return_value="")
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=True):
            handler.get()
            handler.set_status.assert_called_with(400)
            handler.write.assert_called_with({"error": "File path is required"})

    def test_db_missing(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(None, modules=["aird.handlers.api_handlers"]):
            handler.get()
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with(
                {"error": "Database connection not available"}
            )

    def test_success(self):
        handler = self._make_handler()
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch(
            "aird.services.share_service.get_shares_for_path",
            return_value=[{"id": "s1", "paths": ["file.txt"]}],
        ):
            handler.get()
            handler.write.assert_called()
            payload = handler.write.call_args[0][0]
            assert payload["shares"][0]["id"] == "s1"


class TestShareDetailsByIdAPIHandler:
    def _make_handler(self):
        handler = make_request_handler(ShareDetailsByIdAPIHandler)
        handler.get_argument = MagicMock(return_value="share1")
        return handler

    def test_missing_id(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        handler.get_argument = MagicMock(return_value="")
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=True):
            handler.get()
            handler.set_status.assert_called_with(400)
            handler.write.assert_called_with({"error": "Share ID is required"})

    def test_share_not_found(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch(
            "aird.services.share_service.get_share_by_id", return_value=None
        ):
            handler.get()
            handler.set_status.assert_called_with(404)
            handler.write.assert_called_with({"error": "Share not found"})

    def test_success(self):
        handler = self._make_handler()
        share_data = {"id": "share1", "paths": [], "secret_token": "token"}
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch(
            "aird.services.share_service.get_share_by_id", return_value=share_data
        ):
            handler.get()
            payload = handler.write.call_args[0][0]
            assert payload["share"]["id"] == "share1"


class TestShareListAPIHandler:
    def test_feature_disabled(self):
        handler = make_request_handler(ShareListAPIHandler)
        handler.set_status = MagicMock()
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=False):
            handler.get()
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with({"error": "File sharing is disabled"})

    def test_db_missing(self):
        handler = make_request_handler(ShareListAPIHandler)
        handler.set_status = MagicMock()
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(None, modules=["aird.handlers.api_handlers"]):
            handler.get()
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with(
                {"error": "Database connection not available"}
            )

    def test_success(self):
        handler = make_request_handler(ShareListAPIHandler)
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch(
            "aird.services.share_service.get_all_shares",
            return_value={"s1": {"paths": []}},
        ):
            handler.get()
            handler.write.assert_called_with({"shares": {"s1": {"paths": []}}})


class TestFeatureFlagSocketHandler:
    def test_open_sends_current_flags(self):
        handler = make_ws_handler(FeatureFlagSocketHandler)
        with patch.object(
            FeatureFlagSocketHandler.connection_manager,
            "add_connection",
            return_value=True,
        ), patch(
            "aird.handlers.api_handlers.get_current_feature_flags",
            return_value={"in_memory": True, "persisted": False},
        ):

            # Mock authentication
            handler.get_current_user = MagicMock(return_value={"username": "admin"})

            handler.open()
            handler.write_message.assert_called()
            message = json.loads(handler.write_message.call_args[0][0])
            assert message["in_memory"] is True

    def test_open_connection_limit(self):
        handler = make_ws_handler(FeatureFlagSocketHandler)
        # Mock authentication
        handler.get_current_user = MagicMock(return_value={"username": "admin"})

        with patch.object(
            FeatureFlagSocketHandler.connection_manager,
            "add_connection",
            return_value=False,
        ):
            handler.open()
            handler.close.assert_called_with(
                code=1013, reason="Connection limit exceeded"
            )

    def test_send_updates_merges_flags(self):
        with patch(
            "aird.handlers.api_handlers.get_current_feature_flags",
            return_value={"feature_a": True, "feature_b": False},
        ), patch.object(
            FeatureFlagSocketHandler.connection_manager, "broadcast_message"
        ) as mock_broadcast:
            FeatureFlagSocketHandler.send_updates()
            payload = json.loads(mock_broadcast.call_args[0][0])
            assert payload["feature_a"] is True
            assert payload["feature_b"] is False


class TestFileStreamHandler:
    @pytest.mark.asyncio
    async def test_open_requires_auth(self):
        handler = make_ws_handler(FileStreamHandler)
        handler.get_current_user = MagicMock(return_value=None)
        await handler.open("log.txt")
        handler.close.assert_called_with(code=1008, reason="Authentication required")

    @pytest.mark.asyncio
    async def test_open_connection_limit(self):
        handler = make_ws_handler(FileStreamHandler)
        handler.get_current_user = MagicMock(return_value={"username": "user"})
        with patch.object(
            FileStreamHandler.connection_manager, "add_connection", return_value=False
        ):
            await handler.open("log.txt")
            handler.close.assert_called_with(
                code=1013, reason="Connection limit exceeded"
            )

    @pytest.mark.asyncio
    async def test_on_message_invalid_json(self):
        handler = make_ws_handler(FileStreamHandler)
        handler.get_current_user = MagicMock(return_value={"username": "user"})
        await handler.on_message("not-json")
        handler.write_message.assert_called_with(
            json.dumps({"type": "error", "message": "Invalid JSON payload"})
        )

    @pytest.mark.asyncio
    async def test_on_message_missing_action(self):
        handler = make_ws_handler(FileStreamHandler)
        handler.get_current_user = MagicMock(return_value={"username": "user"})
        await handler.on_message(json.dumps({}))
        handler.write_message.assert_called_with(
            json.dumps(
                {"type": "error", "message": "Invalid request: action is required"}
            )
        )


class TestFavoriteToggleAPIHandler:
    def test_feature_disabled(self):
        handler = make_request_handler(FavoriteToggleAPIHandler)
        handler.set_status = MagicMock()
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=False
        ):
            handler.post()
        handler.set_status.assert_called_with(403)
        handler.write.assert_called_with({"error": "Favorites disabled"})

    def test_no_db(self):
        handler = make_request_handler(FavoriteToggleAPIHandler)
        handler.request.body = b'{"path":"a"}'
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(None, modules=["aird.handlers.api_handlers"]):
            handler.post()
        handler.set_status.assert_called_with(500)
        handler.write.assert_called_with({"error": DB_NOT_AVAILABLE_MSG})

    def test_invalid_json(self):
        handler = make_request_handler(FavoriteToggleAPIHandler)
        handler.request.body = b"not-json"
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]):
            handler.post()
        handler.set_status.assert_called_with(400)
        handler.write.assert_called_with({"error": "Invalid JSON"})

    def test_missing_path(self):
        handler = make_request_handler(FavoriteToggleAPIHandler)
        handler.request.body = json.dumps({"path": "  "})
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]):
            handler.post()
        handler.set_status.assert_called_with(400)
        handler.write.assert_called_with({"error": "path is required"})

    def test_username_unresolved(self):
        handler = make_request_handler(FavoriteToggleAPIHandler)
        handler.request.body = json.dumps({"path": " /x "})
        handler.get_current_user = MagicMock(return_value={})
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]):
            handler.post()
        handler.set_status.assert_called_with(401)
        handler.write.assert_called_with({"error": "Could not resolve username"})

    def test_success(self):
        handler = make_request_handler(FavoriteToggleAPIHandler)
        handler.request.body = json.dumps({"path": "docs/a.txt"})
        db = MagicMock()
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(db, modules=["aird.handlers.api_handlers"]), patch(
            "aird.services.favorites_service.toggle_favorite", return_value=True
        ) as mock_toggle:
            handler.post()
        mock_toggle.assert_called_once_with(db, "admin", "docs/a.txt")
        handler.write.assert_called_with(
            {"favorited": True, "path": "docs/a.txt"}
        )


class TestFavoritesListAPIHandler:
    def test_feature_disabled(self):
        handler = make_request_handler(FavoritesListAPIHandler)
        handler.set_status = MagicMock()
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=False
        ):
            handler.get()
        handler.set_status.assert_called_with(403)

    def test_no_db(self):
        handler = make_request_handler(FavoritesListAPIHandler)
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(None, modules=["aird.handlers.api_handlers"]):
            handler.get()
        handler.set_status.assert_called_with(500)
        handler.write.assert_called_with({"error": DB_NOT_AVAILABLE_MSG})

    def test_username_unresolved(self):
        handler = make_request_handler(FavoritesListAPIHandler)
        handler.get_current_user = MagicMock(return_value=None)
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]):
            handler.get()
        handler.set_status.assert_called_with(401)

    def test_success(self):
        handler = make_request_handler(FavoritesListAPIHandler)
        db = MagicMock()
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(db, modules=["aird.handlers.api_handlers"]), patch(
            "aird.services.favorites_service.get_user_favorites",
            return_value=["p1", "p2"],
        ) as mock_gf:
            handler.get()
        mock_gf.assert_called_once_with(db, "admin")
        handler.write.assert_called_with({"favorites": ["p1", "p2"]})


class TestFeatureFlagSocketHandlerExtra:
    def test_open_closes_when_not_authenticated(self):
        handler = make_ws_handler(FeatureFlagSocketHandler)
        handler.get_current_user = MagicMock(return_value=None)
        handler.open()
        handler.close.assert_called_with(code=1008, reason=AUTH_REQUIRED)

    def test_check_origin_delegates(self):
        handler = make_ws_handler(FeatureFlagSocketHandler)
        with patch(
            "aird.handlers.api_handlers.is_valid_websocket_origin",
            return_value=True,
        ) as mock_origin:
            assert handler.check_origin("https://example.com") is True
        mock_origin.assert_called_once()


class TestSuperSearchHandlerExtra:
    def test_get_strips_empty_path_to_blank(self):
        handler = make_request_handler(SuperSearchHandler)
        handler.get_argument = MagicMock(return_value="")
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=True):
            handler.get()
        assert handler.render.call_args[1]["current_path"] == ""


class TestFileListAPIHandlerExtra:
    def test_get_without_db_skips_augment(self):
        handler = make_request_handler(FileListAPIHandler)
        with patch("os.path.abspath", return_value="/root/p"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isdir", return_value=True), patch(
            "aird.handlers.api_handlers.get_files_in_directory",
            return_value=[{"name": "a.txt"}],
        ), patch("aird.handlers.api_handlers.is_video_file", return_value=False), patch(
            "aird.handlers.api_handlers.is_audio_file", return_value=False
        ), patch_db_conn(
            None, modules=["aird.handlers.api_handlers"]
        ), patch(
            "aird.services.share_service.get_all_shares"
        ) as mock_shares:
            handler.get("p")
        mock_shares.assert_not_called()
        payload = handler.write.call_args[0][0]
        assert payload["files"][0]["name"] == "a.txt"


class TestShareDetailsAPIHandlerExtra:
    def test_get_internal_error_on_exception(self):
        handler = make_request_handler(ShareDetailsAPIHandler)
        handler.get_argument = MagicMock(return_value="file.txt")
        handler.set_status = MagicMock()
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=True), patch_db_conn(
            MagicMock(), modules=["aird.handlers.api_handlers"]
        ), patch(
            "aird.services.share_service.get_shares_for_path",
            side_effect=RuntimeError("db down"),
        ):
            handler.get()
        handler.set_status.assert_called_with(500)
        handler.write.assert_called_with(
            {"error": "Failed to retrieve share details"}
        )

    def test_formats_share_with_none_allowed_users(self):
        handler = make_request_handler(ShareDetailsAPIHandler)
        handler.get_argument = MagicMock(return_value="f.txt")
        share = {
            "id": "s1",
            "created": "2020",
            "allowed_users": None,
            "paths": ["f.txt"],
        }
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=True), patch_db_conn(
            MagicMock(), modules=["aird.handlers.api_handlers"]
        ), patch(
            "aird.services.share_service.get_shares_for_path", return_value=[share]
        ):
            handler.get()
        payload = handler.write.call_args[0][0]
        assert payload["shares"][0]["allowed_users"] == []


class TestShareDetailsByIdAPIHandlerExtra:
    def test_get_db_missing(self):
        handler = make_request_handler(ShareDetailsByIdAPIHandler)
        handler.get_argument = MagicMock(return_value="sid")
        handler.set_status = MagicMock()
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=True), patch_db_conn(
            None, modules=["aird.handlers.api_handlers"]
        ):
            handler.get()
        handler.set_status.assert_called_with(500)
        handler.write.assert_called_with({"error": DB_NOT_AVAILABLE_MSG})

    def test_get_internal_error_on_exception(self):
        handler = make_request_handler(ShareDetailsByIdAPIHandler)
        handler.get_argument = MagicMock(return_value="sid")
        handler.set_status = MagicMock()
        with patch("aird.handlers.base_handler.is_feature_enabled", return_value=True), patch_db_conn(
            MagicMock(), modules=["aird.handlers.api_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id", side_effect=OSError("x")
        ):
            handler.get()
        handler.set_status.assert_called_with(500)


class TestFileStreamHandlerExtended:
    @pytest.mark.asyncio
    async def test_open_rejects_non_file(self):
        handler = make_file_stream_ws_handler()
        handler.get_current_user = MagicMock(return_value={"username": "u"})
        handler.get_argument = MagicMock(side_effect=lambda k, default=None: default)
        with patch.object(
            FileStreamHandler.connection_manager, "add_connection", return_value=True
        ), patch("os.path.abspath", return_value="/root/x"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=False):
            await handler.open("missing.txt")
        handler.close.assert_called_with(code=1003, reason="File not found")

    @pytest.mark.asyncio
    async def test_open_invalid_n_lines_uses_default(self):
        handler = make_file_stream_ws_handler()
        handler.get_current_user = MagicMock(return_value={"username": "u"})

        def ga(key, default=None):
            if key == "n":
                return "not-int"
            if key == "filter":
                return None
            return default

        handler.get_argument = MagicMock(side_effect=ga)
        cm = _MockAioLinesCM([])
        with patch.object(
            FileStreamHandler.connection_manager, "add_connection", return_value=True
        ), patch("os.path.abspath", return_value="/root/log"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=True), patch(
            "aird.handlers.api_handlers.aiofiles.open", return_value=cm
        ):
            await handler.open("log.txt")
            await _cancel_stream_task(handler)
        assert handler.line_buffer.maxlen == 1000

    @pytest.mark.asyncio
    async def test_open_negative_n_lines_uses_default(self):
        handler = make_file_stream_ws_handler()
        handler.get_current_user = MagicMock(return_value={"username": "u"})

        def ga(key, default=None):
            if key == "n":
                return "-5"
            if key == "filter":
                return None
            return default

        handler.get_argument = MagicMock(side_effect=ga)
        cm = _MockAioLinesCM([])
        with patch.object(
            FileStreamHandler.connection_manager, "add_connection", return_value=True
        ), patch("os.path.abspath", return_value="/root/log"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=True), patch(
            "aird.handlers.api_handlers.aiofiles.open", return_value=cm
        ):
            await handler.open("log.txt")
            await _cancel_stream_task(handler)
        assert handler.line_buffer.maxlen == 1000

    @pytest.mark.asyncio
    async def test_open_invalid_filter_expression_ignored(self):
        handler = make_file_stream_ws_handler()
        handler.get_current_user = MagicMock(return_value={"username": "u"})

        def ga(key, default=None):
            if key == "filter":
                return "[[[invalid"
            return default

        handler.get_argument = MagicMock(side_effect=ga)
        cm = _MockAioLinesCM(["line1\n"])
        with patch.object(
            FileStreamHandler.connection_manager, "add_connection", return_value=True
        ), patch("os.path.abspath", return_value="/root/log"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=True), patch(
            "aird.handlers.api_handlers.aiofiles.open", return_value=cm
        ):
            await handler.open("log.txt")
            await _cancel_stream_task(handler)
        assert handler.is_streaming is False

    @pytest.mark.asyncio
    async def test_open_read_raises_writes_error(self):
        handler = make_ws_handler(FileStreamHandler)
        handler.get_current_user = MagicMock(return_value={"username": "u"})
        handler.get_argument = MagicMock(side_effect=lambda k, default=None: default)

        def open_raises(*_a, **_k):
            raise OSError("read fail")

        with patch.object(
            FileStreamHandler.connection_manager, "add_connection", return_value=True
        ), patch("os.path.abspath", return_value="/root/log"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=True), patch(
            "aird.handlers.api_handlers.aiofiles.open", side_effect=open_raises
        ):
            await handler.open("log.txt")
        handler.write_message.assert_called_with(
            json.dumps({"type": "error", "message": "read fail"})
        )

    @pytest.mark.asyncio
    async def test_on_message_stop(self):
        handler = make_file_stream_ws_handler()
        await handler.on_message(json.dumps({"action": "stop"}))
        assert handler.is_streaming is False
        assert handler.stop_event.is_set()

    @pytest.mark.asyncio
    async def test_on_message_start_schedules_stream(self):
        handler = make_file_stream_ws_handler()
        handler.file_path = "/tmp/log"
        with patch("asyncio.create_task") as mock_ct:
            await handler.on_message(json.dumps({"action": "start"}))
        mock_ct.assert_called_once()
        created = mock_ct.call_args[0][0]
        if hasattr(created, "close"):
            created.close()

    @pytest.mark.asyncio
    async def test_on_message_lines_resize_buffer(self):
        handler = make_file_stream_ws_handler()
        from collections import deque

        handler.line_buffer = deque(["a", "b"], maxlen=10)
        await handler.on_message(json.dumps({"action": "lines", "lines": 1}))
        assert handler.line_buffer.maxlen == 1

    @pytest.mark.asyncio
    async def test_on_message_filter_and_unknown(self):
        handler = make_ws_handler(FileStreamHandler)
        await handler.on_message(json.dumps({"action": "filter", "filter": "pat"}))
        assert handler.filter_expression == "pat"
        await handler.on_message(json.dumps({"action": "nope"}))
        handler.write_message.assert_called_with(
            json.dumps({"type": "error", "message": "Unknown action"})
        )

    @pytest.mark.asyncio
    async def test_handle_stream_file_missing_path(self):
        handler = make_ws_handler(FileStreamHandler)
        await handler.on_message(
            json.dumps({"action": "stream_file", "file_path": "  "})
        )
        handler.write_message.assert_called_with(
            json.dumps({"type": "error", "message": "file_path is required"})
        )

    @pytest.mark.asyncio
    async def test_handle_stream_file_forbidden(self):
        handler = make_ws_handler(FileStreamHandler)
        with patch("os.path.abspath", return_value="/evil"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=False
        ):
            await handler.on_message(
                json.dumps({"action": "stream_file", "file_path": "x"})
            )
        handler.write_message.assert_called_with(
            json.dumps({"type": "error", "message": "Forbidden path"})
        )

    @pytest.mark.asyncio
    async def test_handle_stream_file_not_found(self):
        handler = make_ws_handler(FileStreamHandler)
        with patch("os.path.abspath", return_value="/root/f"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=False):
            await handler.on_message(
                json.dumps({"action": "stream_file", "file_path": "f"})
            )
        handler.write_message.assert_called_with(
            json.dumps({"type": "error", "message": "File not found"})
        )

    @pytest.mark.asyncio
    async def test_handle_stream_file_success(self):
        handler = make_file_stream_ws_handler()

        async def chunks(_path):
            yield b"ab"
            yield b"c"

        with patch("os.path.abspath", return_value="/root/f"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=True), patch(
            "os.path.getsize", return_value=3
        ), patch(
            "aird.handlers.api_handlers.mimetypes.guess_type",
            return_value=("text/plain", None),
        ), patch(
            "aird.handlers.api_handlers.MMapFileHandler.serve_file_chunk", chunks
        ):
            await handler.on_message(
                json.dumps({"action": "stream_file", "file_path": "f"})
            )
        msgs = [json.loads(c[0][0]) for c in handler.write_message.call_args_list]
        types = [m["type"] for m in msgs]
        assert "stream_start" in types
        assert "stream_data" in types
        assert "stream_end" in types

    @pytest.mark.asyncio
    async def test_handle_stream_file_error(self):
        handler = make_ws_handler(FileStreamHandler)

        async def chunks(_path):
            if False:
                yield b""
            raise RuntimeError("mmap")

        with patch("os.path.abspath", return_value="/root/f"), patch(
            "aird.handlers.api_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=True), patch(
            "os.path.getsize", return_value=1
        ), patch(
            "aird.handlers.api_handlers.mimetypes.guess_type",
            return_value=("application/octet-stream", None),
        ), patch(
            "aird.handlers.api_handlers.MMapFileHandler.serve_file_chunk", chunks
        ):
            await handler.on_message(
                json.dumps({"action": "stream_file", "file_path": "f"})
            )
        last = json.loads(handler.write_message.call_args[0][0])
        assert last["type"] == "error"

    @pytest.mark.asyncio
    async def test_send_line_with_filter_match_error_sends_anyway(self):
        handler = make_file_stream_ws_handler()
        expr = MagicMock()
        expr.matches.side_effect = RuntimeError("bad")
        with patch("aird.handlers.api_handlers.FilterExpression", return_value=expr):
            new_expr, _ = await handler._send_line_with_filter(
                "hello", expr, "f", "f"
            )
        handler.write_message.assert_called()

    @pytest.mark.asyncio
    async def test_stream_file_loop_ws_closed(self):
        handler = make_file_stream_ws_handler()
        handler.file_path = "/tmp/x"
        handler.is_streaming = True
        handler.filter_expression = ""

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=cm)
        cm.__aexit__ = AsyncMock(return_value=None)
        cm.seek = AsyncMock()
        cm.readline = AsyncMock(side_effect=tornado.websocket.WebSocketClosedError())

        with patch("aird.handlers.api_handlers.aiofiles.open", return_value=cm):
            await handler.stream_file()

    def test_on_close_closes_file_logs_oserror(self):
        handler = make_ws_handler(FileStreamHandler)
        mock_f = MagicMock()
        mock_f.close.side_effect = OSError("e")
        handler.file = mock_f
        with patch("logging.Logger.debug"):
            handler.on_close()

    def test_check_origin_file_stream(self):
        handler = make_ws_handler(FileStreamHandler)
        with patch(
            "aird.handlers.api_handlers.is_valid_websocket_origin", return_value=False
        ):
            assert handler.check_origin("x") is False


class TestSuperSearchWebSocketHandlerExtended:
    @pytest.mark.asyncio
    async def test_on_message_invalid_json(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={"username": "u"})
        await handler.on_message("not-json{")
        handler.write_message.assert_called_with(
            json.dumps({"type": "error", "message": "Invalid JSON format"})
        )

    @pytest.mark.asyncio
    async def test_on_message_missing_pattern_or_text(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={"username": "u"})
        await handler.on_message(json.dumps({"pattern": "*.py"}))
        handler.write_message.assert_called()
        payload = json.loads(handler.write_message.call_args[0][0])
        assert payload["type"] == "error"

    @pytest.mark.asyncio
    async def test_open_success_path_logs(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={"username": "alice"})
        with patch.object(
            SuperSearchWebSocketHandler.connection_manager,
            "add_connection",
            return_value=True,
        ), patch("logging.Logger.info"):
            handler.open()

    @pytest.mark.asyncio
    async def test_send_auth_required_swallows_ws_error(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.write_message = MagicMock(
            side_effect=tornado.websocket.WebSocketClosedError()
        )
        handler._send_auth_required_and_close("m", "/r", "reason")
        handler.close.assert_called_with(code=1008, reason="reason")

    @pytest.mark.asyncio
    async def test_perform_search_no_user(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value=None)
        await handler.perform_search("*.txt", "q", "content")
        handler.close.assert_called()

    @pytest.mark.asyncio
    async def test_perform_search_success_completion(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={"username": "u"})
        with patch.object(
            handler,
            "_run_search_walk",
            new_callable=AsyncMock,
            return_value=(2, 10),
        ):
            await handler.perform_search("*.txt", "q", "content")
        last = json.loads(handler.write_message.call_args[0][0])
        assert last["type"] == "search_complete"
        assert last["matches"] == 2

    @pytest.mark.asyncio
    async def test_perform_search_aborts_when_walk_returns_none(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={"username": "u"})
        with patch.object(
            handler, "_run_search_walk", new_callable=AsyncMock, return_value=None
        ):
            await handler.perform_search("*.txt", "q", "content")
        types = [
            json.loads(c[0][0])["type"]
            for c in handler.write_message.call_args_list
            if c[0]
        ]
        assert "search_start" in types
        assert "search_complete" not in types and "no_files" not in types

    @pytest.mark.asyncio
    async def test_perform_search_cancelled(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={"username": "u"})

        async def raise_cancelled(*a, **k):
            raise asyncio.CancelledError

        with patch.object(
            handler, "_run_search_walk", new_callable=AsyncMock, side_effect=raise_cancelled
        ):
            with pytest.raises(asyncio.CancelledError):
                await handler.perform_search("*.txt", "q", "content")

    @pytest.mark.asyncio
    async def test_perform_search_generic_error(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={"username": "u"})
        with patch.object(
            handler,
            "_run_search_walk",
            new_callable=AsyncMock,
            side_effect=RuntimeError("walk boom"),
        ):
            await handler.perform_search("*.txt", "q", "content")
        last = json.loads(handler.write_message.call_args[0][0])
        assert last["type"] == "error"

    def test_send_search_completion_variants(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler._send_search_completion(0, 3)
        m0 = json.loads(handler.write_message.call_args[0][0])
        assert m0["type"] == "no_files"
        handler._send_search_completion(5, 3)
        m1 = json.loads(handler.write_message.call_args[0][0])
        assert m1["type"] == "search_complete"

    def test_file_matches_pattern(self):
        assert SuperSearchWebSocketHandler._file_matches_pattern(
            "src/a.py", "a.py", "*.py"
        )

    def test_send_match_swallows_ws_closed(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.write_message = MagicMock(
            side_effect=tornado.websocket.WebSocketClosedError()
        )
        handler.send_match("p", 1, "line", "q")
        # no raise

    @pytest.mark.asyncio
    async def test_search_file_content_skips_huge_files(self, tmp_path):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        fp = tmp_path / "big.bin"
        fp.write_bytes(b"x")
        mock_stat = MagicMock()
        mock_stat.st_size = MAX_READABLE_FILE_SIZE + 1
        with patch.object(pathlib.Path, "stat", return_value=mock_stat):
            n = await handler._search_file_content(
                pathlib.Path(fp), "big.bin", "needle"
            )
        assert n == 0

    @pytest.mark.asyncio
    async def test_search_one_file_filename_mode_substring(self, tmp_path):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        fp = tmp_path / "hello.txt"
        fp.write_text("x")
        root = pathlib.Path(tmp_path)
        m, seen = await handler._search_one_file(
            fp, root, "hello.txt", "*", "ello", 0, "filename"
        )
        assert seen is True
        assert m == 1

    @pytest.mark.asyncio
    async def test_search_one_file_filename_mode_glob_chars(self, tmp_path):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        fp = tmp_path / "foo.py"
        fp.write_text("")
        root = pathlib.Path(tmp_path)
        m, seen = await handler._search_one_file(
            fp, root, "foo.py", "*", "*.py", 0, "filename"
        )
        assert seen is True
        assert m >= 0

    @pytest.mark.asyncio
    async def test_search_one_file_skips_pattern_mismatch(self, tmp_path):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        fp = tmp_path / "a.txt"
        fp.write_text("x")
        root = pathlib.Path(tmp_path)
        m, seen = await handler._search_one_file(
            fp, root, "a.txt", "*.md", "zzz", 0, "content"
        )
        assert (m, seen) == (0, False)

    @pytest.mark.asyncio
    async def test_search_one_file_relative_error(self, tmp_path):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        other = tmp_path / "sub"
        other.mkdir()
        fp = other / "x.txt"
        fp.write_text("hi")
        wrong_root = pathlib.Path(tmp_path / "other_root")
        wrong_root.mkdir()
        m, seen = await handler._search_one_file(
            fp, wrong_root, "x.txt", "*", "hi", 0, "content"
        )
        assert (m, seen) == (0, False)

    @pytest.mark.asyncio
    async def test_yield_and_check_auth_expired(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value=None)
        ok = await handler._yield_and_check_auth()
        assert ok is False
        handler.close.assert_called()

    @pytest.mark.asyncio
    async def test_await_cancellation_starts_new_when_previous_done(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.search_task = asyncio.create_task(asyncio.sleep(0))
        await asyncio.sleep(0)
        handler.perform_search = AsyncMock()
        await handler._await_cancellation_and_start_new(
            ("*.py", "t", "filename")
        )
        assert handler.search_task is not None

    @pytest.mark.asyncio
    async def test_run_search_walk_returns_none_when_auth_lost(self, tmp_path):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        for i in range(20):
            (tmp_path / f"f{i}.txt").write_text("hello")
        with patch.object(
            handler, "_yield_and_check_auth", new_callable=AsyncMock, return_value=False
        ):
            res = await handler._run_search_walk(
                pathlib.Path(tmp_path).resolve(), "*.txt", "hello", "content"
            )
        assert res is None

    def test_check_origin_super_search(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        with patch(
            "aird.handlers.api_handlers.is_valid_websocket_origin", return_value=True
        ):
            assert handler.check_origin("http://h") is True
