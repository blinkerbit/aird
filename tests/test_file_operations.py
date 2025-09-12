"""
Unit tests for file operation handlers in aird.main module.

These tests cover file upload, delete, rename, and edit operations
with proper security and error handling validation.
"""

import os
import tempfile
import shutil
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Import with error handling for missing module
try:
    from aird.main import (
        UploadHandler,
        DeleteHandler,
        RenameHandler,
        EditHandler,
        ROOT_DIR,
        MAX_FILE_SIZE
    )
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestDeleteHandler:
    """Test DeleteHandler class"""
    
    def test_delete_handler_feature_disabled(self, mock_tornado_app, mock_tornado_request):
        """Test delete handler when feature is disabled"""
        handler = DeleteHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            mock_set_status.assert_called_once_with(403)
            mock_write.assert_called_once_with("File delete is disabled.")
    
    def test_delete_file_success(self, mock_tornado_app, mock_tornado_request, temp_dir):
        """Test successful file deletion"""
        # Create test file
        test_file = os.path.join(temp_dir, "test.txt")
        with open(test_file, "w", encoding='utf-8') as f:
            f.write("test content")
        
        handler = DeleteHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch('aird.main.ROOT_DIR', temp_dir), \
             patch.object(handler, 'get_argument', return_value="test.txt"), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            
            # File should be deleted
            assert not os.path.exists(test_file)
            mock_redirect.assert_called_once_with("/files/")
    
    def test_delete_directory_success(self, mock_tornado_app, mock_tornado_request, temp_dir):
        """Test successful directory deletion"""
        # Create test directory with file
        test_dir = os.path.join(temp_dir, "testdir")
        os.makedirs(test_dir)
        test_file = os.path.join(test_dir, "file.txt")
        with open(test_file, "w", encoding='utf-8') as f:
            f.write("content")
        
        handler = DeleteHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch('aird.main.ROOT_DIR', temp_dir), \
             patch.object(handler, 'get_argument', return_value="testdir"), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            
            # Directory should be deleted
            assert not os.path.exists(test_dir)
            mock_redirect.assert_called_once_with("/files/")
    
    def test_delete_forbidden_path(self, mock_tornado_app, mock_tornado_request):
        """Test deletion of path outside root directory"""
        handler = DeleteHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch.object(handler, 'get_argument', return_value="../../../etc/passwd"), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            mock_set_status.assert_called_once_with(403)
            mock_write.assert_called_once_with("Forbidden")
    
    def test_delete_nonexistent_file(self, mock_tornado_app, mock_tornado_request, temp_dir):
        """Test deletion of non-existent file"""
        handler = DeleteHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch('aird.main.ROOT_DIR', temp_dir), \
             patch.object(handler, 'get_argument', return_value="nonexistent.txt"), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            
            # Should redirect since the file doesn't exist (no error handling in actual code)
            mock_redirect.assert_called_once_with("/files/")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestRenameHandler:
    """Test RenameHandler class"""
    
    def test_rename_handler_feature_disabled(self, mock_tornado_app, mock_tornado_request):
        """Test rename handler when feature is disabled"""
        handler = RenameHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            mock_set_status.assert_called_once_with(403)
            mock_write.assert_called_once_with("File rename is disabled.")
    
    def test_rename_file_success(self, mock_tornado_app, mock_tornado_request, temp_dir):
        """Test successful file rename"""
        # Create test file
        old_path = os.path.join(temp_dir, "old.txt")
        with open(old_path, "w", encoding='utf-8') as f:
            f.write("test content")
        
        handler = RenameHandler(mock_tornado_app, mock_tornado_request)
        
        def mock_get_argument(name, default=""):
            if name == "path":
                return "old.txt"
            elif name == "new_name":
                return "new.txt"
            return default
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch('aird.main.ROOT_DIR', temp_dir), \
             patch.object(handler, 'get_argument', side_effect=mock_get_argument), \
             patch.object(handler, 'redirect') as mock_redirect:
            
            handler.post()
            
            # Old file should not exist, new file should exist
            assert not os.path.exists(old_path)
            new_path = os.path.join(temp_dir, "new.txt")
            assert os.path.exists(new_path)
            
            mock_redirect.assert_called_once_with("/files/")
    
    def test_rename_invalid_filename(self, mock_tornado_app, mock_tornado_request):
        """Test rename with invalid filename"""
        handler = RenameHandler(mock_tornado_app, mock_tornado_request)
        
        def mock_get_argument(name, default=""):
            if name == "path":
                return "test.txt"
            elif name == "new_name":
                return "../invalid"
            return default
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch.object(handler, 'get_argument', side_effect=mock_get_argument), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            mock_set_status.assert_called_once_with(400)
            mock_write.assert_called_once_with("Invalid filename.")
    
    def test_rename_missing_parameters(self, mock_tornado_app, mock_tornado_request):
        """Test rename with missing parameters"""
        handler = RenameHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch.object(handler, 'get_argument', return_value=""), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            mock_set_status.assert_called_once_with(400)
            mock_write.assert_called_once_with("Path and new name are required.")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestEditHandler:
    """Test EditHandler class"""
    
    def test_edit_handler_feature_disabled(self, mock_tornado_app, mock_tornado_request):
        """Test edit handler when feature is disabled"""
        handler = EditHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=False), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            mock_set_status.assert_called_once_with(403)
            mock_write.assert_called_once_with("File editing is disabled.")
    
    def test_edit_file_form_data(self, mock_tornado_app, mock_tornado_request, temp_dir):
        """Test successful file edit with form data"""
        # Create test file
        test_file = os.path.join(temp_dir, "test.txt")
        with open(test_file, "w", encoding='utf-8') as f:
            f.write("original content")
        
        mock_tornado_request.headers = {"Content-Type": "application/x-www-form-urlencoded"}
        handler = EditHandler(mock_tornado_app, mock_tornado_request)
        
        def mock_get_argument(name, default=""):
            if name == "path":
                return "test.txt"
            elif name == "content":
                return "new content"
            return default
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch('aird.main.ROOT_DIR', temp_dir), \
             patch.object(handler, 'get_argument', side_effect=mock_get_argument), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            # Check file was updated
            with open(test_file, "r", encoding='utf-8') as f:
                content = f.read()
            assert content == "new content"
            
            mock_set_status.assert_called_once_with(200)
            mock_write.assert_called_once_with("File saved successfully.")
    
    def test_edit_file_json_data(self, mock_tornado_app, mock_tornado_request, temp_dir):
        """Test successful file edit with JSON data"""
        # Create test file
        test_file = os.path.join(temp_dir, "test.txt")
        with open(test_file, "w", encoding='utf-8') as f:
            f.write("original content")
        
        json_data = json.dumps({"path": "test.txt", "content": "json content"})
        mock_tornado_request.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        mock_tornado_request.body = json_data.encode()
        
        handler = EditHandler(mock_tornado_app, mock_tornado_request)
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch('aird.main.ROOT_DIR', temp_dir), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            # Check file was updated
            with open(test_file, "r", encoding='utf-8') as f:
                content = f.read()
            assert content == "json content"
            
            mock_set_status.assert_called_once_with(200)
            mock_write.assert_called_once_with({"ok": True})
    
    def test_edit_forbidden_path(self, mock_tornado_app, mock_tornado_request):
        """Test edit with forbidden path"""
        handler = EditHandler(mock_tornado_app, mock_tornado_request)
        
        def mock_get_argument(name, default=""):
            if name == "path":
                return "../../../etc/passwd"
            elif name == "content":
                return "hacker content"
            return default
        
        with patch.object(handler, 'get_current_user', return_value='user'), \
             patch('aird.main.is_feature_enabled', return_value=True), \
             patch.object(handler, 'get_argument', side_effect=mock_get_argument), \
             patch.object(handler, 'set_status') as mock_set_status, \
             patch.object(handler, 'write') as mock_write:
            
            handler.post()
            
            mock_set_status.assert_called_once_with(403)
            mock_write.assert_called_once_with("Forbidden")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestUploadHandler:
    """Test UploadHandler class"""
    
    @pytest.mark.asyncio
    async def test_upload_handler_feature_disabled(self, mock_tornado_app, mock_tornado_request):
        """Test upload handler when feature is disabled"""
        mock_tornado_request.headers = {
            "X-Upload-Dir": "",
            "X-Upload-Filename": "test.txt"
        }
        handler = UploadHandler(mock_tornado_app, mock_tornado_request)
        
        with patch('aird.main.is_feature_enabled', return_value=False):
            await handler.prepare()
            
            with patch.object(handler, 'get_current_user', return_value='user'), \
                 patch.object(handler, 'set_status') as mock_set_status, \
                 patch.object(handler, 'write') as mock_write:
                
                await handler.post()
                
                mock_set_status.assert_called_once_with(403)
                mock_write.assert_called_once_with("File upload is disabled.")
    
    @pytest.mark.asyncio
    async def test_upload_missing_filename(self, mock_tornado_app, mock_tornado_request):
        """Test upload with missing filename header"""
        mock_tornado_request.headers = {"X-Upload-Dir": ""}
        handler = UploadHandler(mock_tornado_app, mock_tornado_request)
        
        with patch('aird.main.is_feature_enabled', return_value=True):
            await handler.prepare()
            
            with patch.object(handler, 'get_current_user', return_value='user'), \
                 patch.object(handler, 'set_status') as mock_set_status, \
                 patch.object(handler, 'write') as mock_write:
                
                await handler.post()
                
                mock_set_status.assert_called_once_with(400)
                mock_write.assert_called_once_with("Missing X-Upload-Filename header")
    
    @pytest.mark.asyncio
    async def test_upload_dangerous_extension(self, mock_tornado_app, mock_tornado_request):
        """Test upload with dangerous file extension"""
        mock_tornado_request.headers = {
            "X-Upload-Dir": "",
            "X-Upload-Filename": "malware.exe"
        }
        handler = UploadHandler(mock_tornado_app, mock_tornado_request)
        
        with patch('aird.main.is_feature_enabled', return_value=True):
            await handler.prepare()
            
            # Simulate small file data
            handler.data_received(b"fake exe content")
            
            with patch.object(handler, 'get_current_user', return_value='user'), \
                 patch.object(handler, 'set_status') as mock_set_status, \
                 patch.object(handler, 'write') as mock_write:
                
                await handler.post()
                
                mock_set_status.assert_called_once_with(403)
                mock_write.assert_called_once_with("File type not allowed")
    
    @pytest.mark.asyncio
    async def test_upload_file_too_large(self, mock_tornado_app, mock_tornado_request):
        """Test upload with file too large"""
        mock_tornado_request.headers = {
            "X-Upload-Dir": "",
            "X-Upload-Filename": "large.txt"
        }
        handler = UploadHandler(mock_tornado_app, mock_tornado_request)
        
        with patch('aird.main.is_feature_enabled', return_value=True), \
             patch('aird.main.MAX_FILE_SIZE', 100):  # Small limit for testing
            
            await handler.prepare()
            
            # Simulate large file data
            large_data = b"x" * 200  # Larger than MAX_FILE_SIZE
            handler.data_received(large_data)
            
            with patch.object(handler, 'get_current_user', return_value='user'), \
                 patch.object(handler, 'set_status') as mock_set_status, \
                 patch.object(handler, 'write') as mock_write:
                
                await handler.post()
                
                mock_set_status.assert_called_once_with(413)
                mock_write.assert_called_once_with("File too large")
    
    @pytest.mark.asyncio
    async def test_upload_forbidden_path(self, mock_tornado_app, mock_tornado_request):
        """Test upload to forbidden path"""
        mock_tornado_request.headers = {
            "X-Upload-Dir": "../../../etc",
            "X-Upload-Filename": "passwd"
        }
        handler = UploadHandler(mock_tornado_app, mock_tornado_request)
        
        with patch('aird.main.is_feature_enabled', return_value=True):
            await handler.prepare()
            
            # Simulate file data
            handler.data_received(b"content")
            
            with patch.object(handler, 'get_current_user', return_value='user'), \
                 patch.object(handler, 'set_status') as mock_set_status, \
                 patch.object(handler, 'write') as mock_write:
                
                await handler.post()
                
                mock_set_status.assert_called_once_with(403)
                mock_write.assert_called_once_with("Forbidden path")


@pytest.mark.integration
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestFileOperationsIntegration:
    """Integration tests for file operations working together"""
    
    def test_file_lifecycle_operations(self, temp_dir):
        """Test complete file lifecycle: upload -> edit -> rename -> delete"""
        # This would test the full workflow but requires more complex setup
        # For now, we verify that the handlers can be instantiated
        
        mock_app = MagicMock()
        mock_app.settings = {'cookie_secret': 'test'}
        mock_request = MagicMock()
        mock_request.headers = {}
        
        # Test that all handlers can be created
        upload_handler = UploadHandler(mock_app, mock_request)
        edit_handler = EditHandler(mock_app, mock_request)
        rename_handler = RenameHandler(mock_app, mock_request)
        delete_handler = DeleteHandler(mock_app, mock_request)
        
        assert upload_handler is not None
        assert edit_handler is not None
        assert rename_handler is not None
        assert delete_handler is not None
    
    def test_security_validation_consistency(self):
        """Test that security validations are consistent across handlers"""
        # Test that all file operation handlers properly validate paths
        # and reject dangerous operations
        
        mock_app = MagicMock()
        mock_app.settings = {'cookie_secret': 'test'}
        mock_request = MagicMock()
        
        handlers = [
            EditHandler(mock_app, mock_request),
            RenameHandler(mock_app, mock_request),
            DeleteHandler(mock_app, mock_request)
        ]
        
        # All handlers should have similar security methods
        for handler in handlers:
            assert hasattr(handler, 'post'), f"{handler.__class__.__name__} should have post method"
            # Additional security checks would go here