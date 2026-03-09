
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from aird.handlers.file_op_handlers import (
    UploadHandler, DeleteHandler, RenameHandler, EditHandler, CloudUploadHandler
)
from aird.cloud import CloudProviderError
import json
import os
import asyncio

from tests.handler_helpers import authenticate, prepare_handler


class TestUploadHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {
            'X-Upload-Dir': 'uploads',
            'X-Upload-Filename': 'test.txt'
        }

    def _setup_handler_for_post(self, handler, filename='test.txt', upload_dir='uploads'):
        """Helper to set up handler attributes typically set in prepare()"""
        handler._reject = False
        handler._reject_reason = None
        handler._temp_path = '/tmp/test_upload'
        handler._moved = False
        handler._too_large = False
        handler._writer_task = None
        handler._aiofile = AsyncMock()
        handler._buffer = []
        handler._writing = False
        handler._bytes_received = 100
        handler.upload_dir = upload_dir
        handler.filename = filename

    @pytest.mark.asyncio
    async def test_upload_success(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('aird.handlers.file_op_handlers.ALLOWED_UPLOAD_EXTENSIONS', {'.txt'}), \
             patch('shutil.move') as mock_move, \
             patch('os.makedirs'), \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_move.assert_called()
            assert mock_write.call_args[0][0] == "Upload successful"

    @pytest.mark.asyncio
    async def test_upload_feature_disabled(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(403)
            assert "disabled" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_rejected_missing_header(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        # Simulate rejection in prepare() due to missing header
        handler._reject = True
        handler._reject_reason = "Missing X-Upload-Filename header"
        handler._temp_path = None
        handler._moved = False
        handler._too_large = False
        handler._writer_task = None
        handler._aiofile = None
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(400)
            assert "Missing X-Upload-Filename" in mock_write.call_args[0][0]

    @pytest.mark.asyncio
    async def test_upload_file_too_large(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        handler._too_large = True
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(413)
            assert "too large" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_path_outside_root(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler, upload_dir='../../../etc')
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(403)
            assert "denied" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_invalid_filename_dot(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler, filename='.')
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.basename', return_value='.'), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(400)
            assert "invalid filename" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_invalid_filename_dotdot(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler, filename='..')
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.basename', return_value='..'), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(400)
            assert "invalid filename" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_unsupported_file_type(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler, filename='malware.exe')
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('aird.handlers.file_op_handlers.ALLOWED_UPLOAD_EXTENSIONS', {'.txt', '.pdf'}), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(415)
            assert "unsupported file type" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_filename_too_long(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        long_filename = 'a' * 260 + '.txt'
        self._setup_handler_for_post(handler, filename=long_filename)
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('aird.handlers.file_op_handlers.ALLOWED_UPLOAD_EXTENSIONS', {'.txt'}), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(400)
            assert "too long" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_final_path_outside_root(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        
        # First check passes, second fails (final path validation)
        is_within_root_calls = [True, False]
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', side_effect=is_within_root_calls), \
             patch('aird.handlers.file_op_handlers.ALLOWED_UPLOAD_EXTENSIONS', {'.txt'}), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(403)
            assert "denied" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_move_failure(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('aird.handlers.file_op_handlers.ALLOWED_UPLOAD_EXTENSIONS', {'.txt'}), \
             patch('shutil.move', side_effect=OSError("Permission denied")), \
             patch('os.makedirs'), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            mock_set_status.assert_called_with(500)
            assert "failed to save" in mock_write.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_upload_with_writer_task(self):
        """Test that upload waits for in-flight writes"""
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        
        # Create a completed task
        async def dummy_task():
            pass
        handler._writer_task = asyncio.create_task(dummy_task())
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.realpath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('aird.handlers.file_op_handlers.ALLOWED_UPLOAD_EXTENSIONS', {'.txt'}), \
             patch('shutil.move'), \
             patch('os.makedirs'), \
             patch.object(handler, 'write') as mock_write:
            
            await handler.post()
            assert mock_write.call_args[0][0] == "Upload successful"

    def test_data_received_normal(self):
        handler = UploadHandler(self.mock_app, self.mock_request)
        handler._reject = False
        handler._bytes_received = 0
        handler._too_large = False
        handler._buffer = []
        handler._writing = False
        handler._aiofile = AsyncMock()
        
        with patch('aird.handlers.file_op_handlers.constants_module.MAX_FILE_SIZE', 1000), \
             patch('asyncio.create_task') as mock_create_task:
            handler.data_received(b'test data')
            
        assert handler._bytes_received == 9
        assert b'test data' in handler._buffer
        assert not handler._too_large
        mock_create_task.assert_called_once()

    def test_data_received_too_large(self):
        handler = UploadHandler(self.mock_app, self.mock_request)
        handler._reject = False
        handler._bytes_received = 900
        handler._too_large = False
        handler._buffer = []
        handler._writing = False
        
        with patch('aird.handlers.file_op_handlers.constants_module.MAX_FILE_SIZE', 1000):
            handler.data_received(b'x' * 200)  # Push over limit
            
        assert handler._too_large

    def test_data_received_rejected(self):
        handler = UploadHandler(self.mock_app, self.mock_request)
        handler._reject = True
        handler._bytes_received = 0
        handler._buffer = []
        
        handler.data_received(b'test data')
        
        # Should not process data when rejected
        assert handler._bytes_received == 0
        assert len(handler._buffer) == 0

    def test_on_finish_cleanup_temp_file(self):
        handler = UploadHandler(self.mock_app, self.mock_request)
        handler._temp_path = '/tmp/test_upload'
        handler._moved = False
        
        with patch('os.path.exists', return_value=True), \
             patch('os.remove') as mock_remove:
            handler.on_finish()
            mock_remove.assert_called_with('/tmp/test_upload')

    def test_on_finish_no_cleanup_when_moved(self):
        handler = UploadHandler(self.mock_app, self.mock_request)
        handler._temp_path = '/tmp/test_upload'
        handler._moved = True
        
        with patch('os.path.exists', return_value=True), \
             patch('os.remove') as mock_remove:
            handler.on_finish()
            mock_remove.assert_not_called()

    def test_on_finish_no_temp_path(self):
        handler = UploadHandler(self.mock_app, self.mock_request)
        handler._temp_path = None
        handler._moved = False
        
        # Should not raise
        handler.on_finish()

class TestUploadHandlerDynamicMaxSize:
    """Tests for dynamic max file size enforcement in UploadHandler"""

    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {
            'X-Upload-Dir': 'uploads',
            'X-Upload-Filename': 'test.txt'
        }

    def _setup_handler_for_post(self, handler, filename='test.txt', upload_dir='uploads'):
        handler._reject = False
        handler._reject_reason = None
        handler._temp_path = '/tmp/test_upload'
        handler._moved = False
        handler._too_large = False
        handler._writer_task = None
        handler._aiofile = AsyncMock()
        handler._buffer = []
        handler._writing = False
        handler._bytes_received = 100
        handler.upload_dir = upload_dir
        handler.filename = filename

    def test_data_received_uses_dynamic_max_file_size(self):
        """Test data_received checks against constants_module.MAX_FILE_SIZE"""
        handler = UploadHandler(self.mock_app, self.mock_request)
        handler._reject = False
        handler._bytes_received = 0
        handler._too_large = False
        handler._buffer = []
        handler._writing = False

        custom_limit = 10 * 1024 * 1024  # 10 MB

        with patch('aird.handlers.file_op_handlers.constants_module') as mock_constants, \
             patch('asyncio.create_task'):
            mock_constants.MAX_FILE_SIZE = custom_limit

            small_chunk = b'x' * (5 * 1024 * 1024)
            handler.data_received(small_chunk)
            assert handler._too_large is False

            handler.data_received(small_chunk)
            handler.data_received(small_chunk)
            assert handler._too_large is True

    def test_data_received_respects_larger_limit(self):
        """Test data_received allows larger files when limit is increased"""
        handler = UploadHandler(self.mock_app, self.mock_request)
        handler._reject = False
        handler._bytes_received = 0
        handler._too_large = False
        handler._buffer = []
        handler._writing = False

        large_limit = 2 * 1024 * 1024 * 1024  # 2 GB

        with patch('aird.handlers.file_op_handlers.constants_module') as mock_constants, \
             patch('asyncio.create_task'):
            mock_constants.MAX_FILE_SIZE = large_limit

            chunk = b'x' * (500 * 1024 * 1024)
            handler.data_received(chunk)
            assert handler._too_large is False
            assert handler._bytes_received == 500 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_too_large_error_shows_configured_limit(self):
        """Test that 413 error message reflects the configured limit"""
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        handler._too_large = True

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('aird.handlers.file_op_handlers.constants_module') as mock_constants, \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:

            mock_constants.UPLOAD_CONFIG = {'max_file_size_mb': 256}

            await handler.post()

            mock_set_status.assert_called_with(413)
            msg = mock_write.call_args[0][0]
            assert "256" in msg
            assert "too large" in msg.lower()

    @pytest.mark.asyncio
    async def test_too_large_error_shows_default_limit(self):
        """Test that 413 error shows 512 MB when using default config"""
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        handler._too_large = True

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('aird.handlers.file_op_handlers.constants_module') as mock_constants, \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:

            mock_constants.UPLOAD_CONFIG = {'max_file_size_mb': 512}

            await handler.post()

            mock_set_status.assert_called_with(413)
            msg = mock_write.call_args[0][0]
            assert "512" in msg

    @pytest.mark.asyncio
    async def test_too_large_error_shows_custom_large_limit(self):
        """Test that 413 error shows custom large limit (e.g. 2048 MB)"""
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        self._setup_handler_for_post(handler)
        handler._too_large = True

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('aird.handlers.file_op_handlers.constants_module') as mock_constants, \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:

            mock_constants.UPLOAD_CONFIG = {'max_file_size_mb': 2048}

            await handler.post()

            mock_set_status.assert_called_with(413)
            msg = mock_write.call_args[0][0]
            assert "2048" in msg


class TestDeleteHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_delete_file(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="test.txt")
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/test.txt'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.remove') as mock_remove, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            mock_remove.assert_called_with('/root/test.txt')
            mock_redirect.assert_called()

    def test_delete_feature_disabled(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(403)
            assert "disabled" in mock_write.call_args[0][0].lower()

    def test_delete_access_denied(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="../../../etc/passwd")
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/etc/passwd'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(403)
            assert "denied" in mock_write.call_args[0][0].lower()

    def test_delete_directory(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="subdir")
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/subdir'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('os.path.isfile', return_value=False), \
             patch('os.listdir', return_value=[]), \
             patch('shutil.rmtree') as mock_rmtree, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            mock_rmtree.assert_called_with('/root/subdir')
            mock_redirect.assert_called()

    def test_delete_with_parent_path(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="subdir/file.txt")
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/subdir/file.txt'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.remove'), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            # Should redirect to parent directory
            mock_redirect.assert_called_with('/files/subdir')

class TestRenameHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}

    def test_rename_file(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': 'new.txt'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=True), \
             patch('os.rename') as mock_rename, \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            mock_rename.assert_called()
            mock_redirect.assert_called()

    def test_rename_feature_disabled(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(403)
            assert "disabled" in mock_write.call_args[0][0].lower()

    def test_rename_empty_path(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': '', 'new_name': 'new.txt'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(400)
            assert "required" in mock_write.call_args[0][0].lower()

    def test_rename_empty_new_name(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': ''}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(400)
            assert "required" in mock_write.call_args[0][0].lower()

    def test_rename_invalid_filename_dot(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': '.'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(400)
            assert "invalid filename" in mock_write.call_args[0][0].lower()

    def test_rename_invalid_filename_dotdot(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': '..'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(400)
            assert "invalid filename" in mock_write.call_args[0][0].lower()

    def test_rename_invalid_filename_with_slash(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': 'path/name'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(400)
            assert "invalid filename" in mock_write.call_args[0][0].lower()

    def test_rename_invalid_filename_with_backslash(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': 'path\\name'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(400)
            assert "invalid filename" in mock_write.call_args[0][0].lower()

    def test_rename_filename_too_long(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        long_name = 'a' * 260
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': long_name}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(400)
            assert "too long" in mock_write.call_args[0][0].lower()

    def test_rename_access_denied(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': '../etc/passwd', 'new_name': 'new.txt'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(403)
            assert "denied" in mock_write.call_args[0][0].lower()

    def test_rename_file_not_found(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'nonexistent.txt', 'new_name': 'new.txt'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(404)
            assert "not found" in mock_write.call_args[0][0].lower()

    def test_rename_os_error(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': 'new.txt'}.get(k, d))
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=True), \
             patch('os.rename', side_effect=OSError("Permission denied")), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(500)
            assert "failed" in mock_write.call_args[0][0].lower()

class TestEditHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {}

    def test_edit_file(self):
        handler = prepare_handler(EditHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'test.txt', 'content': 'new content'}.get(k, d))
        
        mock_resolved_path = MagicMock()
        mock_resolved_path.__str__.return_value = '/root/test.txt'
        mock_resolved_path.parents = [] # ROOT_DIR not in parents for success case
        
        mock_path = MagicMock()
        mock_path.resolve.return_value = mock_resolved_path

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('pathlib.Path.absolute', return_value=mock_path), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.makedirs'), \
             patch('tempfile.NamedTemporaryFile') as mock_temp, \
             patch('os.replace') as mock_replace, \
             patch.object(handler, 'write') as mock_write:
            
            mock_temp.return_value.__enter__.return_value.name = '/tmp/temp'
            
            handler.post()
            mock_replace.assert_called()
            assert mock_write.call_args[0][0] == "File saved successfully."

    def test_edit_file_access_denied_json(self):
        handler = prepare_handler(EditHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        handler.request.body = json.dumps({'path': 'test.txt', 'content': 'new content'}).encode('utf-8')
        
        mock_resolved_path = MagicMock()
        mock_resolved_path.__str__.return_value = '/root/test.txt'
        
        mock_path = MagicMock()
        mock_path.resolve.return_value = mock_resolved_path

        # Simulate access denied by returning False from is_within_root
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('pathlib.Path.absolute', return_value=mock_path), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(403)
            # Should have written an error message
            mock_write.assert_called()

    def test_edit_feature_disabled(self):
        handler = prepare_handler(EditHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(403)
            assert "disabled" in mock_write.call_args[0][0].lower()

    def test_edit_invalid_json(self):
        handler = prepare_handler(EditHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.headers = {'Content-Type': 'application/json'}
        handler.request.body = b'{"invalid json'
        
        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(400)
            assert "invalid" in mock_write.call_args[0][0].lower()

    def test_edit_file_not_found(self):
        handler = prepare_handler(EditHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'nonexistent.txt', 'content': 'new content'}.get(k, d))
        
        mock_resolved_path = MagicMock()
        mock_resolved_path.__str__.return_value = '/root/nonexistent.txt'
        
        mock_path = MagicMock()
        mock_path.resolve.return_value = mock_resolved_path

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('pathlib.Path.absolute', return_value=mock_path), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(404)
            assert "not found" in mock_write.call_args[0][0].lower()

    def test_edit_save_error(self):
        handler = prepare_handler(EditHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'test.txt', 'content': 'new content'}.get(k, d))
        
        mock_resolved_path = MagicMock()
        mock_resolved_path.__str__.return_value = '/root/test.txt'
        
        mock_path = MagicMock()
        mock_path.resolve.return_value = mock_resolved_path

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('pathlib.Path.absolute', return_value=mock_path), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.makedirs'), \
             patch('tempfile.NamedTemporaryFile', side_effect=OSError("Disk full")), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            mock_set_status.assert_called_with(500)
            assert "error" in mock_write.call_args[0][0].lower()

    def test_edit_json_response(self):
        handler = prepare_handler(EditHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        handler.request.body = json.dumps({'path': 'test.txt', 'content': 'new content'}).encode('utf-8')
        
        mock_resolved_path = MagicMock()
        mock_resolved_path.__str__.return_value = '/root/test.txt'
        
        mock_path = MagicMock()
        mock_path.resolve.return_value = mock_resolved_path

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('pathlib.Path.absolute', return_value=mock_path), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.makedirs'), \
             patch('tempfile.NamedTemporaryFile') as mock_temp, \
             patch('os.replace'), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            mock_temp.return_value.__enter__.return_value.name = '/tmp/temp'
            
            handler.post()
            mock_set_status.assert_called_with(200)
            # Should return JSON response
            assert mock_write.call_args[0][0] == {"ok": True}

class TestCloudUploadHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.files = {'file': [{'body': b'content', 'filename': 'test.txt'}]}

    @pytest.mark.asyncio
    async def test_cloud_upload(self):
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        mock_provider = MagicMock()
        mock_provider.upload_file.return_value = MagicMock(to_dict=lambda: {'id': '123'})
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch.object(handler, 'write') as mock_write:
            await handler.post("provider1")
            mock_write.assert_called()
            assert mock_write.call_args[0][0]['file']['id'] == '123'

    @pytest.mark.asyncio
    async def test_cloud_upload_provider_not_found(self):
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        mock_manager = MagicMock()
        mock_manager.get.return_value = None
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.post("nonexistent_provider")
            mock_set_status.assert_called_with(404)
            assert "not configured" in str(mock_write.call_args[0][0]).lower()

    @pytest.mark.asyncio
    async def test_cloud_upload_no_file(self):
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.files = {}  # No files
        
        mock_provider = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.post("provider1")
            mock_set_status.assert_called_with(400)
            assert "no file" in str(mock_write.call_args[0][0]).lower()

    @pytest.mark.asyncio
    async def test_cloud_upload_file_too_large(self):
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        # Create a large file body
        large_body = b'x' * (600 * 1024 * 1024)  # 600MB (over 512MB limit)
        handler.request.files = {'file': [{'body': large_body, 'filename': 'large.bin'}]}
        
        mock_provider = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch('aird.handlers.file_op_handlers.constants_module.MAX_FILE_SIZE', 512 * 1024 * 1024), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.post("provider1")
            mock_set_status.assert_called_with(413)
            assert "too large" in str(mock_write.call_args[0][0]).lower()

    @pytest.mark.asyncio
    async def test_cloud_upload_provider_error(self):
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        mock_provider = MagicMock()
        mock_provider.upload_file.side_effect = CloudProviderError("Upload failed")
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch('asyncio.to_thread', side_effect=CloudProviderError("Upload failed")), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.post("provider1")
            mock_set_status.assert_called_with(400)
            assert "upload failed" in str(mock_write.call_args[0][0]).lower()

    @pytest.mark.asyncio
    async def test_cloud_upload_generic_error(self):
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        mock_provider = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch('asyncio.to_thread', side_effect=Exception("Unknown error")), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            await handler.post("provider1")
            mock_set_status.assert_called_with(500)
            assert "failed" in str(mock_write.call_args[0][0]).lower()

    @pytest.mark.asyncio
    async def test_cloud_upload_with_parent_id(self):
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_body_argument = MagicMock(return_value="parent_folder_123")
        
        mock_cloud_file = MagicMock()
        mock_cloud_file.to_dict.return_value = {'id': '456', 'name': 'test.txt'}
        
        mock_provider = MagicMock()
        mock_provider.upload_file.return_value = mock_cloud_file
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch('asyncio.to_thread', return_value=mock_cloud_file), \
             patch.object(handler, 'write') as mock_write:
            await handler.post("provider1")
            mock_write.assert_called()
            assert mock_write.call_args[0][0]['file']['id'] == '456'

    @pytest.mark.asyncio
    async def test_cloud_upload_empty_file(self):
        """Test uploading an empty file is allowed"""
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.files = {'file': [{'body': b'', 'filename': 'empty.txt'}]}
        
        mock_cloud_file = MagicMock()
        mock_cloud_file.to_dict.return_value = {'id': '789', 'name': 'empty.txt'}
        
        mock_provider = MagicMock()
        mock_provider.upload_file.return_value = mock_cloud_file
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch('asyncio.to_thread', return_value=mock_cloud_file), \
             patch.object(handler, 'write') as mock_write:
            await handler.post("provider1")
            mock_write.assert_called()
            assert mock_write.call_args[0][0]['file']['id'] == '789'

    @pytest.mark.asyncio
    async def test_cloud_upload_sanitize_filename(self):
        """Test that filenames are sanitized"""
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.files = {'file': [{'body': b'content', 'filename': '../../../etc/passwd'}]}
        
        mock_cloud_file = MagicMock()
        mock_cloud_file.to_dict.return_value = {'id': '123', 'name': 'passwd'}
        
        mock_provider = MagicMock()
        mock_provider.upload_file.return_value = mock_cloud_file
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch('asyncio.to_thread', return_value=mock_cloud_file) as mock_to_thread, \
             patch('aird.handlers.file_op_handlers.sanitize_cloud_filename', return_value='passwd') as mock_sanitize, \
             patch.object(handler, 'write'):
            await handler.post("provider1")
            # Verify sanitize was called
            mock_sanitize.assert_called_once()

    @pytest.mark.asyncio
    async def test_cloud_upload_default_filename(self):
        """Test default filename when none provided"""
        handler = prepare_handler(CloudUploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.files = {'file': [{'body': b'content', 'filename': None}]}
        
        mock_cloud_file = MagicMock()
        mock_cloud_file.to_dict.return_value = {'id': '123', 'name': 'upload.bin'}
        
        mock_provider = MagicMock()
        mock_provider.upload_file.return_value = mock_cloud_file
        mock_manager = MagicMock()
        mock_manager.get.return_value = mock_provider
        self.mock_app.settings['cloud_manager'] = mock_manager
        
        with patch('asyncio.to_thread', return_value=mock_cloud_file), \
             patch('aird.handlers.file_op_handlers.sanitize_cloud_filename', return_value='upload.bin'), \
             patch.object(handler, 'write') as mock_write:
            await handler.post("provider1")
            mock_write.assert_called()


from aird.handlers.file_op_handlers import (
    CreateFolderHandler, CopyHandler, MoveHandler, BulkHandler, path_to_rel
)


class TestPathToRel:
    def test_normal_path(self):
        with patch('aird.handlers.file_op_handlers.ROOT_DIR', '/root'):
            with patch('os.path.relpath', return_value='subdir/file.txt'):
                assert path_to_rel('/root/subdir/file.txt') == 'subdir/file.txt'

    def test_backslash_replaced(self):
        with patch('aird.handlers.file_op_handlers.ROOT_DIR', 'C:\\root'):
            with patch('os.path.relpath', return_value='subdir\\file.txt'):
                assert path_to_rel('C:\\root\\subdir\\file.txt') == 'subdir/file.txt'

    def test_exception_returns_original(self):
        with patch('aird.handlers.file_op_handlers.ROOT_DIR', '/root'):
            with patch('os.path.relpath', side_effect=ValueError("error")):
                assert path_to_rel('/some/path') == '/some/path'


class TestCreateFolderHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {}

    def test_create_folder_success(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': 'docs', 'name': 'newfolder'}.get(k, d))
        handler.request.headers = {}

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=False), \
             patch('os.makedirs'), \
             patch.object(handler, 'redirect') as mock_redirect:
            handler.post()
            mock_redirect.assert_called_once()
            assert '/files/' in mock_redirect.call_args[0][0]

    def test_create_folder_feature_disabled(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    def test_create_folder_invalid_name_empty(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': '', 'name': ''}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_create_folder_invalid_name_dot(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': '', 'name': '..'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_create_folder_name_with_slash(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': '', 'name': 'a/b'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_create_folder_name_too_long(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': '', 'name': 'a' * 256}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_create_folder_outside_root(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': '../..', 'name': 'evil'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    def test_create_folder_already_exists(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': '', 'name': 'existing'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(409)

    def test_create_folder_makedirs_error(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': '', 'name': 'newfolder'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=False), \
             patch('os.makedirs', side_effect=OSError("disk full")), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(500)

    def test_create_folder_json_response(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': 'docs', 'name': 'sub'}.get(k, d))
        handler.request.headers = {'Accept': 'application/json'}

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=False), \
             patch('os.makedirs'), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert result['ok'] is True
            assert result['path'] == 'docs/sub'

    def test_create_folder_at_root_json(self):
        handler = prepare_handler(CreateFolderHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'parent': '', 'name': 'rootfolder'}.get(k, d))
        handler.request.headers = {'Accept': 'application/json'}

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=False), \
             patch('os.makedirs'), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert result['path'] == 'rootfolder'


class TestCopyHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {}

    def test_copy_file_success(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', side_effect=[True, False]), \
             patch('os.path.isdir', return_value=False), \
             patch('shutil.copy2') as mock_copy, \
             patch.object(handler, 'redirect'):
            handler.post()
            mock_copy.assert_called_once()

    def test_copy_directory_success(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'dir1', 'dest': 'dir2'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', side_effect=[True, False]), \
             patch('os.path.isdir', return_value=True), \
             patch('shutil.copytree') as mock_copytree, \
             patch.object(handler, 'redirect'):
            handler.post()
            mock_copytree.assert_called_once()

    def test_copy_feature_disabled(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    def test_copy_missing_path(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': '', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_copy_source_not_found(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'gone.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(404)

    def test_copy_dest_exists(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(409)

    def test_copy_os_error(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', side_effect=[True, False]), \
             patch('os.path.isdir', return_value=False), \
             patch('shutil.copy2', side_effect=OSError("fail")), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(500)

    def test_copy_json_response(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))
        handler.request.headers = {'Accept': 'application/json'}

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', side_effect=[True, False]), \
             patch('os.path.isdir', return_value=False), \
             patch('shutil.copy2'), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            assert mock_write.call_args[0][0] == {"ok": True}

    def test_copy_access_denied(self):
        handler = prepare_handler(CopyHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(403)


class TestMoveHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {}

    def test_move_success(self):
        handler = prepare_handler(MoveHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', side_effect=[True, False]), \
             patch('shutil.move') as mock_move, \
             patch.object(handler, 'redirect'):
            handler.post()
            mock_move.assert_called_once()

    def test_move_feature_disabled(self):
        handler = prepare_handler(MoveHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    def test_move_missing_args(self):
        handler = prepare_handler(MoveHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': '', 'dest': ''}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_move_access_denied(self):
        handler = prepare_handler(MoveHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    def test_move_source_not_found(self):
        handler = prepare_handler(MoveHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(404)

    def test_move_dest_exists(self):
        handler = prepare_handler(MoveHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(409)

    def test_move_os_error(self):
        handler = prepare_handler(MoveHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', side_effect=[True, False]), \
             patch('shutil.move', side_effect=OSError("fail")), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(500)

    def test_move_json_response(self):
        handler = prepare_handler(MoveHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d='': {'path': 'a.txt', 'dest': 'b.txt'}.get(k, d))
        handler.request.headers = {'Accept': 'application/json'}

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', side_effect=[True, False]), \
             patch('shutil.move'), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            assert mock_write.call_args[0][0] == {"ok": True}


class TestBulkHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {}

    def test_bulk_invalid_json(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = b'not json'

        with patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_bulk_missing_paths(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({"action": "delete"}).encode()

        with patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_bulk_empty_paths(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({"action": "delete", "paths": []}).encode()

        with patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_bulk_delete_file_success(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({"action": "delete", "paths": ["file.txt"]}).encode()

        with patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.remove'), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert result['ok'] is True
            assert result['results'][0]['ok'] is True

    def test_bulk_unsupported_action(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({"action": "unknown", "paths": ["file.txt"]}).encode()

        with patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('os.path.exists', return_value=True), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert result['ok'] is False
            assert 'unsupported' in result['results'][0]['error']

    def test_bulk_non_string_path(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({"action": "delete", "paths": [123]}).encode()

        with patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert result['results'][0]['ok'] is False
            assert 'invalid path' in result['results'][0]['error']

    def test_bulk_path_outside_root(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({"action": "delete", "paths": ["../../etc/passwd"]}).encode()

        with patch('aird.handlers.file_op_handlers.is_within_root', return_value=False), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert 'access denied' in result['results'][0]['error']

    def test_bulk_path_not_found(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({"action": "delete", "paths": ["gone.txt"]}).encode()

        with patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('os.path.exists', return_value=False), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert 'not found' in result['results'][0]['error']

    def test_bulk_delete_folder_disabled(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({"action": "delete", "paths": ["mydir"]}).encode()

        with patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=False), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert 'folder delete disabled' in result['results'][0]['error']

    def test_bulk_add_to_share(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({
            "action": "add_to_share",
            "paths": ["file.txt"],
            "share_id": "share123"
        }).encode()

        mock_share = {"paths": ["other.txt"]}
        with patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('os.path.exists', return_value=True), \
             patch('aird.handlers.file_op_handlers.get_share_by_id', return_value=mock_share), \
             patch('aird.handlers.file_op_handlers.update_share', return_value=True), \
             patch('aird.handlers.file_op_handlers.constants_module') as mock_const, \
             patch.object(handler, 'write') as mock_write:
            mock_const.DB_CONN = MagicMock()
            handler.post()
            result = mock_write.call_args[0][0]
            assert result['results'][0]['ok'] is True

    def test_bulk_add_to_share_missing_id(self):
        handler = prepare_handler(BulkHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.request.body = json.dumps({
            "action": "add_to_share",
            "paths": ["file.txt"]
        }).encode()

        with patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('os.path.exists', return_value=True), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            result = mock_write.call_args[0][0]
            assert 'share_id required' in result['results'][0]['error']


class TestDeleteHandlerAdditional:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'cookie_secret': 'test_secret'}
        self.mock_request.headers = {}

    def test_delete_folder_feature_disabled(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="mydir")

        def is_enabled_side_effect(key, default=True):
            if key == 'folder_delete':
                return False
            return True

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', side_effect=is_enabled_side_effect), \
             patch('os.path.abspath', return_value='/root/mydir'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    def test_delete_file_feature_disabled(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="file.txt")

        def is_enabled_side_effect(key, default=True):
            if key == 'file_delete':
                return False
            return True

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', side_effect=is_enabled_side_effect), \
             patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('os.path.isfile', return_value=True), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    def test_delete_non_empty_folder_no_recursive(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'mydir', 'recursive': '0'}.get(k, d or ''))

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/mydir'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=True), \
             patch('os.path.isfile', return_value=False), \
             patch('os.listdir', return_value=['child.txt']), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(400)

    def test_delete_not_found(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="ghost.txt")

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/ghost.txt'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isdir', return_value=False), \
             patch('os.path.isfile', return_value=False), \
             patch.object(handler, 'set_status') as mock_status:
            handler.post()
            mock_status.assert_called_with(404)

    def test_delete_json_response(self):
        handler = prepare_handler(DeleteHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(return_value="file.txt")
        handler.request.headers = {'Accept': 'application/json'}

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', return_value='/root/file.txt'), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.isfile', return_value=True), \
             patch('os.remove'), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            assert mock_write.call_args[0][0] == {"ok": True}

    def test_rename_json_response(self):
        handler = prepare_handler(RenameHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        handler.get_argument = MagicMock(side_effect=lambda k, d=None: {'path': 'old.txt', 'new_name': 'new.txt'}.get(k, d))
        handler.request.headers = {'Accept': 'application/json'}

        with patch('aird.handlers.file_op_handlers.is_feature_enabled', return_value=True), \
             patch('os.path.abspath', side_effect=lambda p: p), \
             patch('aird.handlers.file_op_handlers.is_within_root', return_value=True), \
             patch('os.path.exists', return_value=True), \
             patch('os.rename'), \
             patch.object(handler, 'write') as mock_write:
            handler.post()
            assert mock_write.call_args[0][0] == {"ok": True}
