
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from aird.handlers.file_op_handlers import (
    UploadHandler, DeleteHandler, RenameHandler, EditHandler, CloudUploadHandler
)
import json
import os

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
    
    @pytest.mark.asyncio
    async def test_upload_success(self):
        handler = prepare_handler(UploadHandler(self.mock_app, self.mock_request))
        authenticate(handler, role='user')
        
        # Mock prepare-related attributes that are usually set in prepare()
        handler._reject = False
        handler._temp_path = '/tmp/test'
        handler._moved = False
        handler._too_large = False
        handler._writer_task = None
        handler._aiofile = AsyncMock()
        handler.upload_dir = 'uploads'
        handler.filename = 'test.txt'
        
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
             patch('os.path.isfile', return_value=True), \
             patch('os.makedirs'), \
             patch('tempfile.NamedTemporaryFile') as mock_temp, \
             patch('os.replace') as mock_replace, \
             patch.object(handler, 'write') as mock_write:
            
            mock_temp.return_value.__enter__.return_value.name = '/tmp/temp'
            
            handler.post()
            mock_replace.assert_called()
            assert mock_write.call_args[0][0] == "File saved successfully."

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
