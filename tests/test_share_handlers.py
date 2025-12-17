
import json

import pytest
from unittest.mock import patch, MagicMock, AsyncMock, ANY
from aird.handlers.share_handlers import (
    ShareFilesHandler,
    ShareCreateHandler,
    ShareRevokeHandler,
    ShareUpdateHandler,
    TokenVerificationHandler,
    SharedListHandler,
    SharedFileHandler,
)
from aird.cloud import CloudProviderError

from tests.handler_helpers import authenticate, patch_db_conn, prepare_handler


class TestShareFilesHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_get_share_page(self):
        handler = prepare_handler(ShareFilesHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'render') as mock_render:

            handler.get()
            mock_render.assert_called_with("share.html", shares={})

    def test_get_feature_disabled(self):
        handler = prepare_handler(ShareFilesHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.set_status = MagicMock()

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'write') as mock_write:

            handler.get()
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with("Feature disabled: File sharing is currently disabled by administrator")


class TestShareCreateHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def _build_handler(self, body):
        handler = prepare_handler(ShareCreateHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.check_xsrf_cookie = MagicMock()
        handler.request.headers = {'X-XSRFToken': 'token'}
        handler.get_cookie = MagicMock(return_value='token')
        handler.request.body = json.dumps(body).encode('utf-8')
        handler.set_status = MagicMock()
        return handler

    def test_create_share_success(self):
        handler = self._build_handler({'paths': ['test.txt']})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/test.txt'), \
             patch('aird.handlers.share_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.insert_share', return_value=True), \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            mock_write.assert_called()
            assert 'id' in mock_write.call_args[0][0]

    def test_create_share_feature_disabled(self):
        handler = self._build_handler({'paths': ['test.txt']})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with({"error": "File sharing is disabled"})

    def test_create_share_no_valid_files(self):
        handler = self._build_handler({'paths': ['missing.txt']})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/missing.txt'), \
             patch('aird.handlers.share_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=False), \
             patch('os.path.isdir', return_value=False), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.remove_share_cloud_dir') as mock_remove, \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "No valid files or directories"})
            mock_remove.assert_called()

    def test_create_share_dynamic_requires_directory(self):
        handler = self._build_handler({'paths': [], 'share_type': 'dynamic'})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.remove_share_cloud_dir') as mock_remove, \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "No valid directories for dynamic share"})
            mock_remove.assert_called()

    def test_create_share_cloud_error(self):
        handler = self._build_handler({'paths': [{'type': 'cloud', 'path': 'cloud://item'}]})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.download_cloud_items', side_effect=CloudProviderError("boom")), \
             patch('aird.handlers.share_handlers.remove_share_cloud_dir') as mock_remove, \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "boom"})
            mock_remove.assert_called()

    def test_create_share_db_missing(self):
        handler = self._build_handler({'paths': ['file.txt']})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.share_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch_db_conn(None, modules=['aird.handlers.share_handlers']), \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            handler.set_status.assert_called_with(500)
            mock_write.assert_called_with({"error": "Database connection not available"})


class TestShareRevokeHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_revoke_share_redirect(self):
        handler = prepare_handler(ShareRevokeHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="share1")

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.delete_share') as mock_delete, \
             patch('aird.handlers.share_handlers.remove_share_cloud_dir'), \
             patch.object(handler, 'redirect') as mock_redirect:

            handler.post()
            assert mock_delete.call_args[0][1] == "share1"
            mock_redirect.assert_called_with('/share')

    def test_revoke_share_json_response(self):
        handler = prepare_handler(ShareRevokeHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="share1")
        handler.request.headers = {'Accept': 'application/json'}

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.delete_share'), \
             patch('aird.handlers.share_handlers.remove_share_cloud_dir'), \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            mock_write.assert_called_with({'ok': True})


class TestShareUpdateHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def _build_handler(self, body):
        handler = prepare_handler(ShareUpdateHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.check_xsrf_cookie = MagicMock()
        handler.request.body = json.dumps(body).encode('utf-8')
        handler.set_status = MagicMock()
        return handler

    def test_update_share_missing_id(self):
        handler = self._build_handler({})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "Share ID is required"})

    def test_update_share_not_found(self):
        handler = self._build_handler({'share_id': 'missing'})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value=None), \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            handler.set_status.assert_called_with(404)
            mock_write.assert_called_with({"error": "Share not found"})

    def test_update_share_db_missing(self):
        handler = self._build_handler({'share_id': 'share1'})

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch_db_conn(None, modules=['aird.handlers.share_handlers']), \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            handler.set_status.assert_called_with(500)
            mock_write.assert_called_with({"error": "Database connection not available"})

    def test_update_share_dynamic_cloud_error(self):
        body = {
            'share_id': 'share1',
            'paths': [{'type': 'cloud', 'path': 'cloud://file'}],
            'share_type': 'static',
        }
        handler = self._build_handler(body)

        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value={'paths': [], 'share_type': 'static'}), \
             patch('aird.handlers.share_handlers.download_cloud_items', side_effect=CloudProviderError("boom")), \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "boom"})

    def test_update_share_disable_token_generates_new(self):
        body = {
            'share_id': 'share1',
            'disable_token': False,
            'paths': [],
        }
        handler = self._build_handler(body)

        share_data = {'paths': [], 'share_type': 'static', 'secret_token': None}
        with patch('aird.handlers.share_handlers.is_feature_enabled', return_value=True), \
             patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', side_effect=[share_data, {'secret_token': 'newtoken'}]), \
             patch('aird.handlers.share_handlers.update_share', return_value=True), \
             patch.object(handler, 'write') as mock_write:

            handler.post()
            response = mock_write.call_args[0][0]
            assert response['success'] is True
            assert response['new_token'] == 'newtoken'


class TestTokenVerificationHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_verify_token_success(self):
        handler = prepare_handler(TokenVerificationHandler(self.mock_app, self.mock_request))
        handler.request.body = json.dumps({'token': 'secret'}).encode('utf-8')

        with patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value={'secret_token': 'secret'}), \
             patch.object(handler, 'write') as mock_write:

            handler.post("share1")
            mock_write.assert_called_with({"success": True})

    def test_verify_token_missing_token(self):
        handler = prepare_handler(TokenVerificationHandler(self.mock_app, self.mock_request))
        handler.request.body = json.dumps({}).encode('utf-8')
        handler.set_status = MagicMock()

        with patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value={'secret_token': 'secret'}), \
             patch.object(handler, 'write') as mock_write:

            handler.post("share1")
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "Token is required"})

    def test_verify_token_invalid(self):
        handler = prepare_handler(TokenVerificationHandler(self.mock_app, self.mock_request))
        handler.request.body = json.dumps({'token': 'bad'}).encode('utf-8')
        handler.set_status = MagicMock()

        with patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value={'secret_token': 'secret'}), \
             patch.object(handler, 'write') as mock_write:

            handler.post("share1")
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with({"error": "Invalid token"})


class TestSharedListHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_get_shared_list_static(self):
        handler = prepare_handler(SharedListHandler(self.mock_app, self.mock_request))

        share_data = {'paths': ['test.txt'], 'share_type': 'static', 'secret_token': None}
        with patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value=share_data), \
             patch('aird.handlers.share_handlers.is_share_expired', return_value=False), \
             patch('aird.handlers.share_handlers.filter_files_by_patterns', return_value=['test.txt']), \
             patch.object(handler, 'render') as mock_render:

            handler.get("share1")
            mock_render.assert_called()
            assert mock_render.call_args[1]['share_id'] == "share1"

    def test_shared_list_expired(self):
        handler = prepare_handler(SharedListHandler(self.mock_app, self.mock_request))
        handler.set_status = MagicMock()

        share_data = {'paths': ['test.txt'], 'share_type': 'static'}
        with patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value=share_data), \
             patch('aird.handlers.share_handlers.is_share_expired', return_value=True), \
             patch.object(handler, 'write') as mock_write:

            handler.get("share1")
            handler.set_status.assert_called_with(410)
            mock_write.assert_called_with("Share expired: This share is no longer available")

    def test_shared_list_requires_token_redirects(self):
        handler = prepare_handler(SharedListHandler(self.mock_app, self.mock_request))
        handler.redirect = MagicMock()
        # Ensure get_cookie returns None (not a MagicMock) to simulate missing cookie
        handler.get_cookie = MagicMock(return_value=None)
        # Mock request.headers.get to return empty string for Authorization header
        handler.request.headers = MagicMock()
        handler.request.headers.get = MagicMock(return_value='')

        share_data = {'paths': ['test.txt'], 'share_type': 'static', 'secret_token': 'abc'}
        with patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value=share_data), \
             patch('aird.handlers.share_handlers.is_share_expired', return_value=False):

            handler.get("share1")
            handler.redirect.assert_called_with("/shared/share1/verify")


class TestSharedFileHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    @pytest.mark.asyncio
    async def test_get_shared_file_success(self):
        handler = prepare_handler(SharedFileHandler(self.mock_app, self.mock_request))
        handler.set_status = MagicMock()

        share_data = {'paths': ['test.txt'], 'share_type': 'static', 'secret_token': None}
        with patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value=share_data), \
             patch('aird.handlers.share_handlers.is_share_expired', return_value=False), \
             patch('aird.handlers.share_handlers.filter_files_by_patterns', return_value=['test.txt']), \
             patch('os.path.abspath', return_value='/root/test.txt'), \
             patch('aird.handlers.share_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('aird.handlers.share_handlers.MainHandler') as mock_main_handler:

            mock_main_handler.serve_file = AsyncMock()

            await handler.get("share1", "test.txt")
            mock_main_handler.serve_file.assert_awaited_with(handler, '/root/test.txt')

    @pytest.mark.asyncio
    async def test_shared_file_requires_token(self):
        handler = prepare_handler(SharedFileHandler(self.mock_app, self.mock_request))
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        # Ensure get_cookie returns None (not a MagicMock)
        handler.get_cookie = MagicMock(return_value=None)
        # Mock request.headers.get to return empty string for Authorization header
        handler.request.headers = MagicMock()
        handler.request.headers.get = MagicMock(return_value='')

        share_data = {'paths': ['test.txt'], 'share_type': 'static', 'secret_token': 'secret'}
        with patch_db_conn(MagicMock(), modules=['aird.handlers.share_handlers']), \
             patch('aird.handlers.share_handlers.get_share_by_id', return_value=share_data), \
             patch('aird.handlers.share_handlers.is_share_expired', return_value=False):

            await handler.get("share1", "test.txt")
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with("Access denied: Invalid or expired access token")
