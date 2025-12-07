
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
        
        with patch('aird.handlers.file_op_handlers.MAX_FILE_SIZE', 1000), \
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
        
        with patch('aird.handlers.file_op_handlers.MAX_FILE_SIZE', 1000):
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
        
        with patch('aird.handlers.file_op_handlers.MAX_FILE_SIZE', 512 * 1024 * 1024), \
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
