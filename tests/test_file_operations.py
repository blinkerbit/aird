"""
Unit tests for file operation handlers in aird.main module.
"""

import os
import tempfile
import shutil
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from aird.main import (
    UploadHandler,
    DeleteHandler,
    RenameHandler,
    EditHandler,
    ROOT_DIR,
    MAX_FILE_SIZE
)


class MockApplication:
    """Mock Tornado application for testing handlers"""
    
    def __init__(self):
        self.settings = {
            'cookie_secret': 'test_secret',
            'template_path': '/fake/templates'
        }


class MockRequest:
    """Mock HTTP request for testing handlers"""
    
    def __init__(self, method="POST", path="/", body=b"", headers=None, arguments=None):
        self.method = method
        self.path = path
        self.body = body
        self.headers = headers or {}
        self.arguments = arguments or {}


class TestDeleteHandler:
    """Test DeleteHandler class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.app = MockApplication()
        self.temp_dir = tempfile.mkdtemp()
        self.original_root = ROOT_DIR
        
        # Patch ROOT_DIR for testing
        self.root_patcher = patch('aird.main.ROOT_DIR', self.temp_dir)
        self.root_patcher.start()
    
    def teardown_method(self):
        """Clean up test fixtures"""
        self.root_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @patch('aird.main.is_feature_enabled', return_value=False)
    def test_delete_handler_disabled(self, mock_feature):
        """Test delete handler when feature is disabled"""
        request = MockRequest(arguments={'path': ['test.txt']})
        handler = DeleteHandler(self.app, request)
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        handler.get_argument = MagicMock(return_value="test.txt")
        
        handler.post()
        
        handler.set_status.assert_called_once_with(403)
        handler.write.assert_called_once_with("File delete is disabled.")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_delete_file_success(self, mock_feature):
        """Test successful file deletion"""
        # Create test file
        test_file = os.path.join(self.temp_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")
        
        request = MockRequest()
        handler = DeleteHandler(self.app, request)
        
        # Mock methods
        handler.get_argument = MagicMock(return_value="test.txt")
        handler.redirect = MagicMock()
        
        handler.post()
        
        # File should be deleted
        assert not os.path.exists(test_file)
        handler.redirect.assert_called_once_with("/files/")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_delete_directory_success(self, mock_feature):
        """Test successful directory deletion"""
        # Create test directory with file
        test_dir = os.path.join(self.temp_dir, "testdir")
        os.makedirs(test_dir)
        test_file = os.path.join(test_dir, "file.txt")
        with open(test_file, "w") as f:
            f.write("content")
        
        request = MockRequest()
        handler = DeleteHandler(self.app, request)
        
        # Mock methods
        handler.get_argument = MagicMock(return_value="testdir")
        handler.redirect = MagicMock()
        
        handler.post()
        
        # Directory should be deleted
        assert not os.path.exists(test_dir)
        handler.redirect.assert_called_once_with("/files/")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_delete_forbidden_path(self, mock_feature):
        """Test deletion of path outside root directory"""
        request = MockRequest()
        handler = DeleteHandler(self.app, request)
        
        # Mock methods
        handler.get_argument = MagicMock(return_value="../../../etc/passwd")
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(403)
        handler.write.assert_called_once_with("Forbidden")


class TestRenameHandler:
    """Test RenameHandler class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.app = MockApplication()
        self.temp_dir = tempfile.mkdtemp()
        
        # Patch ROOT_DIR for testing
        self.root_patcher = patch('aird.main.ROOT_DIR', self.temp_dir)
        self.root_patcher.start()
    
    def teardown_method(self):
        """Clean up test fixtures"""
        self.root_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @patch('aird.main.is_feature_enabled', return_value=False)
    def test_rename_handler_disabled(self, mock_feature):
        """Test rename handler when feature is disabled"""
        request = MockRequest()
        handler = RenameHandler(self.app, request)
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(403)
        handler.write.assert_called_once_with("File rename is disabled.")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_rename_file_success(self, mock_feature):
        """Test successful file rename"""
        # Create test file
        old_path = os.path.join(self.temp_dir, "old.txt")
        with open(old_path, "w") as f:
            f.write("test content")
        
        request = MockRequest()
        handler = RenameHandler(self.app, request)
        
        # Mock methods
        def mock_get_argument(name, default=""):
            if name == "path":
                return "old.txt"
            elif name == "new_name":
                return "new.txt"
            return default
        
        handler.get_argument = MagicMock(side_effect=mock_get_argument)
        handler.redirect = MagicMock()
        
        handler.post()
        
        # Old file should not exist, new file should exist
        assert not os.path.exists(old_path)
        new_path = os.path.join(self.temp_dir, "new.txt")
        assert os.path.exists(new_path)
        
        handler.redirect.assert_called_once_with("/files/")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_rename_invalid_filename(self, mock_feature):
        """Test rename with invalid filename"""
        request = MockRequest()
        handler = RenameHandler(self.app, request)
        
        # Mock methods
        def mock_get_argument(name, default=""):
            if name == "path":
                return "test.txt"
            elif name == "new_name":
                return "../invalid"
            return default
        
        handler.get_argument = MagicMock(side_effect=mock_get_argument)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with("Invalid filename.")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_rename_missing_parameters(self, mock_feature):
        """Test rename with missing parameters"""
        request = MockRequest()
        handler = RenameHandler(self.app, request)
        
        # Mock methods
        handler.get_argument = MagicMock(return_value="")
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with("Path and new name are required.")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_rename_long_filename(self, mock_feature):
        """Test rename with filename too long"""
        request = MockRequest()
        handler = RenameHandler(self.app, request)
        
        # Mock methods
        def mock_get_argument(name, default=""):
            if name == "path":
                return "test.txt"
            elif name == "new_name":
                return "a" * 256  # Too long
            return default
        
        handler.get_argument = MagicMock(side_effect=mock_get_argument)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with("Filename too long.")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_rename_nonexistent_file(self, mock_feature):
        """Test rename of non-existent file"""
        request = MockRequest()
        handler = RenameHandler(self.app, request)
        
        # Mock methods
        def mock_get_argument(name, default=""):
            if name == "path":
                return "nonexistent.txt"
            elif name == "new_name":
                return "new.txt"
            return default
        
        handler.get_argument = MagicMock(side_effect=mock_get_argument)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(404)
        handler.write.assert_called_once_with("File not found")


class TestEditHandler:
    """Test EditHandler class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.app = MockApplication()
        self.temp_dir = tempfile.mkdtemp()
        
        # Patch ROOT_DIR for testing
        self.root_patcher = patch('aird.main.ROOT_DIR', self.temp_dir)
        self.root_patcher.start()
    
    def teardown_method(self):
        """Clean up test fixtures"""
        self.root_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @patch('aird.main.is_feature_enabled', return_value=False)
    def test_edit_handler_disabled(self, mock_feature):
        """Test edit handler when feature is disabled"""
        request = MockRequest()
        handler = EditHandler(self.app, request)
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(403)
        handler.write.assert_called_once_with("File editing is disabled.")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_edit_file_form_data(self, mock_feature):
        """Test successful file edit with form data"""
        # Create test file
        test_file = os.path.join(self.temp_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("original content")
        
        request = MockRequest(headers={"Content-Type": "application/x-www-form-urlencoded"})
        handler = EditHandler(self.app, request)
        
        # Mock methods
        def mock_get_argument(name, default=""):
            if name == "path":
                return "test.txt"
            elif name == "content":
                return "new content"
            return default
        
        handler.get_argument = MagicMock(side_effect=mock_get_argument)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        # Check file was updated
        with open(test_file, "r") as f:
            content = f.read()
        assert content == "new content"
        
        handler.set_status.assert_called_once_with(200)
        handler.write.assert_called_once_with("File saved successfully.")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_edit_file_json_data(self, mock_feature):
        """Test successful file edit with JSON data"""
        # Create test file
        test_file = os.path.join(self.temp_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("original content")
        
        json_data = json.dumps({"path": "test.txt", "content": "json content"})
        request = MockRequest(
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=json_data.encode()
        )
        handler = EditHandler(self.app, request)
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        # Check file was updated
        with open(test_file, "r") as f:
            content = f.read()
        assert content == "json content"
        
        handler.set_status.assert_called_once_with(200)
        handler.write.assert_called_once_with({"ok": True})
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_edit_invalid_json(self, mock_feature):
        """Test edit with invalid JSON"""
        request = MockRequest(
            headers={"Content-Type": "application/json"},
            body=b"invalid json"
        )
        handler = EditHandler(self.app, request)
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with("Invalid JSON body")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_edit_forbidden_path(self, mock_feature):
        """Test edit with forbidden path"""
        request = MockRequest()
        handler = EditHandler(self.app, request)
        
        # Mock methods
        def mock_get_argument(name, default=""):
            if name == "path":
                return "../../../etc/passwd"
            elif name == "content":
                return "hacker content"
            return default
        
        handler.get_argument = MagicMock(side_effect=mock_get_argument)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(403)
        handler.write.assert_called_once_with("Forbidden")
    
    @patch('aird.main.is_feature_enabled', return_value=True)
    def test_edit_nonexistent_file(self, mock_feature):
        """Test edit of non-existent file"""
        request = MockRequest()
        handler = EditHandler(self.app, request)
        
        # Mock methods
        def mock_get_argument(name, default=""):
            if name == "path":
                return "nonexistent.txt"
            elif name == "content":
                return "new content"
            return default
        
        handler.get_argument = MagicMock(side_effect=mock_get_argument)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.post()
        
        handler.set_status.assert_called_once_with(404)
        handler.write.assert_called_once_with("File not found")


class TestUploadHandler:
    """Test UploadHandler class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.app = MockApplication()
        self.temp_dir = tempfile.mkdtemp()
        
        # Patch ROOT_DIR for testing
        self.root_patcher = patch('aird.main.ROOT_DIR', self.temp_dir)
        self.root_patcher.start()
    
    def teardown_method(self):
        """Clean up test fixtures"""
        self.root_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @pytest.mark.asyncio
    @patch('aird.main.is_feature_enabled', return_value=False)
    async def test_upload_handler_disabled(self, mock_feature):
        """Test upload handler when feature is disabled"""
        request = MockRequest(headers={
            "X-Upload-Dir": "",
            "X-Upload-Filename": "test.txt"
        })
        handler = UploadHandler(self.app, request)
        
        await handler.prepare()
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        await handler.post()
        
        handler.set_status.assert_called_once_with(403)
        handler.write.assert_called_once_with("File upload is disabled.")
    
    @pytest.mark.asyncio
    @patch('aird.main.is_feature_enabled', return_value=True)
    async def test_upload_missing_filename(self, mock_feature):
        """Test upload with missing filename header"""
        request = MockRequest(headers={
            "X-Upload-Dir": ""
        })
        handler = UploadHandler(self.app, request)
        
        await handler.prepare()
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        await handler.post()
        
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with("Missing X-Upload-Filename header")
    
    @pytest.mark.asyncio
    @patch('aird.main.is_feature_enabled', return_value=True)
    async def test_upload_dangerous_extension(self, mock_feature):
        """Test upload with dangerous file extension"""
        request = MockRequest(headers={
            "X-Upload-Dir": "",
            "X-Upload-Filename": "malware.exe"
        })
        handler = UploadHandler(self.app, request)
        
        await handler.prepare()
        
        # Simulate small file data
        handler.data_received(b"fake exe content")
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        await handler.post()
        
        handler.set_status.assert_called_once_with(403)
        handler.write.assert_called_once_with("File type not allowed")
    
    @pytest.mark.asyncio
    @patch('aird.main.is_feature_enabled', return_value=True)
    async def test_upload_filename_too_long(self, mock_feature):
        """Test upload with filename too long"""
        long_filename = "a" * 256 + ".txt"
        request = MockRequest(headers={
            "X-Upload-Dir": "",
            "X-Upload-Filename": long_filename
        })
        handler = UploadHandler(self.app, request)
        
        await handler.prepare()
        
        # Simulate small file data
        handler.data_received(b"content")
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        await handler.post()
        
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with("Filename too long")
    
    @pytest.mark.asyncio
    @patch('aird.main.is_feature_enabled', return_value=True)
    @patch('aird.main.MAX_FILE_SIZE', 100)  # Small limit for testing
    async def test_upload_file_too_large(self, mock_feature):
        """Test upload with file too large"""
        request = MockRequest(headers={
            "X-Upload-Dir": "",
            "X-Upload-Filename": "large.txt"
        })
        handler = UploadHandler(self.app, request)
        
        await handler.prepare()
        
        # Simulate large file data
        large_data = b"x" * 200  # Larger than MAX_FILE_SIZE
        handler.data_received(large_data)
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        await handler.post()
        
        handler.set_status.assert_called_once_with(413)
        handler.write.assert_called_once_with("File too large")
    
    @pytest.mark.asyncio
    @patch('aird.main.is_feature_enabled', return_value=True)
    async def test_upload_forbidden_path(self, mock_feature):
        """Test upload to forbidden path"""
        request = MockRequest(headers={
            "X-Upload-Dir": "../../../etc",
            "X-Upload-Filename": "passwd"
        })
        handler = UploadHandler(self.app, request)
        
        await handler.prepare()
        
        # Simulate file data
        handler.data_received(b"content")
        
        # Mock methods
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        await handler.post()
        
        handler.set_status.assert_called_once_with(403)
        handler.write.assert_called_once_with("Forbidden path")
    
    @pytest.mark.asyncio
    @patch('aird.main.is_feature_enabled', return_value=True)
    async def test_upload_invalid_filename_patterns(self, mock_feature):
        """Test upload with invalid filename patterns"""
        invalid_filenames = [".", "..", ""]
        
        for filename in invalid_filenames:
            request = MockRequest(headers={
                "X-Upload-Dir": "",
                "X-Upload-Filename": filename
            })
            handler = UploadHandler(self.app, request)
            
            await handler.prepare()
            handler.data_received(b"content")
            
            # Mock methods
            handler.set_status = MagicMock()
            handler.write = MagicMock()
            
            await handler.post()
            
            handler.set_status.assert_called_with(400)
            handler.write.assert_called_with("Invalid filename")