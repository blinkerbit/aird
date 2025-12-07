import json
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aird.handlers.api_handlers import (
    FeatureFlagSocketHandler,
    FileListAPIHandler,
    FileStreamHandler,
    ShareDetailsAPIHandler,
    ShareDetailsByIdAPIHandler,
    ShareListAPIHandler,
    SuperSearchHandler,
    SuperSearchWebSocketHandler,
    UserSearchAPIHandler,
    WebSocketStatsHandler,
)
from tests.handler_helpers import authenticate, patch_db_conn, prepare_handler


def make_request_handler(handler_cls):
    """Helper for BaseHandler descendants."""
    app = MagicMock()
    app.settings = {'cookie_secret': 'test_secret'}
    request = MagicMock()
    request.protocol = "http"
    handler = prepare_handler(handler_cls(app, request))
    authenticate(handler, role='admin')
    return handler


def make_ws_handler(handler_cls):
    app = MagicMock()
    app.settings = {'cookie_secret': 'test_secret'}
    request = MagicMock()
    request.headers = {}
    request.path = "/ws"
    request.connection = MagicMock()
    handler = handler_cls(app, request)
    handler.write_message = MagicMock()
    handler.close = MagicMock()
    handler.request = request
    return handler


class TestFileListAPIHandler:
    def test_get_success(self):
        handler = make_request_handler(FileListAPIHandler)
        with patch('os.path.abspath', return_value='/root/path'), \
             patch('aird.handlers.api_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('aird.handlers.api_handlers.get_files_in_directory', return_value=[{'name': 'file.txt'}]), \
             patch('aird.handlers.api_handlers.is_video_file', return_value=False), \
             patch('aird.handlers.api_handlers.is_audio_file', return_value=False), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.get_all_shares', return_value={'share': {'paths': ['path/file.txt']}}):

            handler.get("path")
            handler.write.assert_called()
            payload = handler.write.call_args[0][0]
            assert payload['files'][0]['name'] == 'file.txt'

    def test_get_forbidden_path(self):
        handler = make_request_handler(FileListAPIHandler)
        handler.set_status = MagicMock()
        with patch('os.path.abspath', return_value='/bad'), \
             patch('aird.handlers.api_handlers.is_within_root', return_value=False):
            handler.get("bad")
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with("Access denied")

    def test_missing_directory(self):
        handler = make_request_handler(FileListAPIHandler)
        handler.set_status = MagicMock()
        with patch('os.path.abspath', return_value='/root/path'), \
             patch('aird.handlers.api_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False):
            handler.get("missing")
            handler.set_status.assert_called_with(404)
            handler.write.assert_called_with("Directory not found")

    def test_get_handles_exception(self):
        handler = make_request_handler(FileListAPIHandler)
        handler.set_status = MagicMock()
        with patch('os.path.abspath', return_value='/root/path'), \
             patch('aird.handlers.api_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('aird.handlers.api_handlers.get_files_in_directory', side_effect=RuntimeError("boom")):
            handler.get("path")
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with("boom")


class TestSuperSearchHandler:
    def test_get_renders_when_enabled(self):
        handler = make_request_handler(SuperSearchHandler)
        handler.get_argument = MagicMock(return_value="//nested//")
        with patch('aird.main.is_feature_enabled', return_value=True):
            handler.get()
            handler.render.assert_called()
            assert handler.render.call_args[1]['current_path'] == "nested"

    def test_get_feature_disabled(self):
        handler = make_request_handler(SuperSearchHandler)
        handler.set_status = MagicMock()
        with patch('aird.main.is_feature_enabled', return_value=False):
            handler.get()
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with("Feature disabled: Super Search is currently disabled by administrator")


class TestSuperSearchWebSocketHandler:
    def test_open_requires_auth(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value=None)
        handler.open()
        handler.write_message.assert_called()
        handler.close.assert_called_with(code=1008, reason="Authentication required")

    def test_open_connection_limit(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value={'username': 'admin'})
        with patch.object(SuperSearchWebSocketHandler.connection_manager, 'add_connection', return_value=False):
            handler.open()
            handler.close.assert_called_with(code=1013, reason="Connection limit exceeded")

    @pytest.mark.asyncio
    async def test_on_message_auth_failure(self):
        handler = make_ws_handler(SuperSearchWebSocketHandler)
        handler.get_current_user = MagicMock(return_value=None)
        await handler.on_message(json.dumps({'pattern': '*.py', 'search_text': 'foo'}))
        handler.close.assert_called_with(code=1008, reason="Authentication expired")


class TestUserSearchAPIHandler:
    def test_requires_db_connection(self):
        handler = make_request_handler(UserSearchAPIHandler)
        handler.set_status = MagicMock()
        handler.get_argument = MagicMock(return_value="bob")
        with patch_db_conn(None, modules=['aird.handlers.api_handlers']):
            handler.get()
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with({"error": "Database not available"})

    def test_returns_empty_for_short_query(self):
        handler = make_request_handler(UserSearchAPIHandler)
        handler.get_argument = MagicMock(return_value=" ")
        with patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']):
            handler.get()
            handler.write.assert_called_with({"users": []})

    def test_handles_search_error(self):
        handler = make_request_handler(UserSearchAPIHandler)
        handler.set_status = MagicMock()
        handler.get_argument = MagicMock(return_value="bob")
        with patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.search_users', side_effect=RuntimeError("nope")):
            handler.get()
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with({"error": "nope"})

    def test_search_success(self):
        handler = make_request_handler(UserSearchAPIHandler)
        handler.get_argument = MagicMock(return_value="alice")
        with patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.search_users', return_value=[{'username': 'alice'}]):
            handler.get()
            handler.write.assert_called_with({"users": [{'username': 'alice'}]})


class TestShareDetailsAPIHandler:
    def _make_handler(self):
        handler = make_request_handler(ShareDetailsAPIHandler)
        handler.get_argument = MagicMock(return_value="file.txt")
        return handler

    def test_feature_disabled(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        with patch('aird.main.is_feature_enabled', return_value=False):
            handler.get()
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with({"error": "File sharing is disabled"})

    def test_missing_path(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        handler.get_argument = MagicMock(return_value="")
        with patch('aird.main.is_feature_enabled', return_value=True):
            handler.get()
            handler.set_status.assert_called_with(400)
            handler.write.assert_called_with({"error": "File path is required"})

    def test_db_missing(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        with patch('aird.main.is_feature_enabled', return_value=True), \
             patch_db_conn(None, modules=['aird.handlers.api_handlers']):
            handler.get()
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with({"error": "Database connection not available"})

    def test_success(self):
        handler = self._make_handler()
        with patch('aird.main.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.get_shares_for_path', return_value=[{'id': 's1', 'paths': ['file.txt']}]):
            handler.get()
            handler.write.assert_called()
            payload = handler.write.call_args[0][0]
            assert payload['shares'][0]['id'] == 's1'


class TestShareDetailsByIdAPIHandler:
    def _make_handler(self):
        handler = make_request_handler(ShareDetailsByIdAPIHandler)
        handler.get_argument = MagicMock(return_value="share1")
        return handler

    def test_missing_id(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        handler.get_argument = MagicMock(return_value="")
        with patch('aird.main.is_feature_enabled', return_value=True):
            handler.get()
            handler.set_status.assert_called_with(400)
            handler.write.assert_called_with({"error": "Share ID is required"})

    def test_share_not_found(self):
        handler = self._make_handler()
        handler.set_status = MagicMock()
        with patch('aird.main.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.get_share_by_id', return_value=None):
            handler.get()
            handler.set_status.assert_called_with(404)
            handler.write.assert_called_with({"error": "Share not found"})

    def test_success(self):
        handler = self._make_handler()
        share_data = {'id': 'share1', 'paths': [], 'secret_token': 'token'}
        with patch('aird.main.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.get_share_by_id', return_value=share_data):
            handler.get()
            payload = handler.write.call_args[0][0]
            assert payload['share']['id'] == 'share1'


class TestShareListAPIHandler:
    def test_feature_disabled(self):
        handler = make_request_handler(ShareListAPIHandler)
        handler.set_status = MagicMock()
        with patch('aird.main.is_feature_enabled', return_value=False):
            handler.get()
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with({"error": "File sharing is disabled"})

    def test_db_missing(self):
        handler = make_request_handler(ShareListAPIHandler)
        handler.set_status = MagicMock()
        with patch('aird.main.is_feature_enabled', return_value=True), \
             patch_db_conn(None, modules=['aird.handlers.api_handlers']):
            handler.get()
            handler.set_status.assert_called_with(500)
            handler.write.assert_called_with({"error": "Database connection not available"})

    def test_success(self):
        handler = make_request_handler(ShareListAPIHandler)
        with patch('aird.main.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.get_all_shares', return_value={'s1': {'paths': []}}):
            handler.get()
            handler.write.assert_called_with({"shares": {'s1': {'paths': []}}})


class TestFeatureFlagSocketHandler:
    def test_open_sends_current_flags(self):
        handler = make_ws_handler(FeatureFlagSocketHandler)
        with patch.object(FeatureFlagSocketHandler.connection_manager, 'add_connection', return_value=True), \
             patch('aird.constants.FEATURE_FLAGS', {'in_memory': True}), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.load_feature_flags', return_value={'persisted': False}):
            
            # Mock authentication
            handler.get_current_user = MagicMock(return_value={'username': 'admin'})
            
            handler.open()
            handler.write_message.assert_called()
            message = json.loads(handler.write_message.call_args[0][0])
            assert message['in_memory'] is True

    def test_open_connection_limit(self):
        handler = make_ws_handler(FeatureFlagSocketHandler)
        # Mock authentication
        handler.get_current_user = MagicMock(return_value={'username': 'admin'})
        
        with patch.object(FeatureFlagSocketHandler.connection_manager, 'add_connection', return_value=False):
            handler.open()
            handler.close.assert_called_with(code=1013, reason="Connection limit exceeded")

    def test_send_updates_merges_flags(self):
        with patch('aird.constants.FEATURE_FLAGS', {'feature_a': True}), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.api_handlers']), \
             patch('aird.handlers.api_handlers.load_feature_flags', return_value={'feature_b': False}), \
             patch.object(FeatureFlagSocketHandler.connection_manager, 'broadcast_message') as mock_broadcast:
            FeatureFlagSocketHandler.send_updates()
            payload = json.loads(mock_broadcast.call_args[0][0])
            assert payload['feature_a'] is True
            assert payload['feature_b'] is False


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
        handler.get_current_user = MagicMock(return_value={'username': 'user'})
        with patch.object(FileStreamHandler.connection_manager, 'add_connection', return_value=False):
            await handler.open("log.txt")
            handler.close.assert_called_with(code=1013, reason="Connection limit exceeded")

    @pytest.mark.asyncio
    async def test_on_message_invalid_json(self):
        handler = make_ws_handler(FileStreamHandler)
        handler.get_current_user = MagicMock(return_value={'username': 'user'})
        await handler.on_message("not-json")
        handler.write_message.assert_called_with(json.dumps({'type': 'error', 'message': 'Invalid JSON payload'}))

    @pytest.mark.asyncio
    async def test_on_message_missing_action(self):
        handler = make_ws_handler(FileStreamHandler)
        handler.get_current_user = MagicMock(return_value={'username': 'user'})
        await handler.on_message(json.dumps({}))
        handler.write_message.assert_called_with(json.dumps({'type': 'error', 'message': 'Invalid request: action is required'}))


class TestWebSocketStatsHandler:
    def test_requires_admin(self):
        handler = make_request_handler(WebSocketStatsHandler)
        handler.set_status = MagicMock()
        handler.is_admin_user = MagicMock(return_value=False)
        handler.get()
        handler.set_status.assert_called_with(403)
        handler.write.assert_called_with("Access denied: You don't have permission to perform this action")

    def test_returns_stats(self):
        handler = make_request_handler(WebSocketStatsHandler)
        handler.is_admin_user = MagicMock(return_value=True)
        stats = {'connections': 1}
        with patch.object(FeatureFlagSocketHandler.connection_manager, 'get_stats', return_value=stats), \
             patch.object(FileStreamHandler.connection_manager, 'get_stats', return_value=stats), \
             patch.object(SuperSearchWebSocketHandler.connection_manager, 'get_stats', return_value=stats):
            handler.get()
            handler.write.assert_called()
            payload = json.loads(handler.write.call_args[0][0])
            assert 'feature_flags' in payload

