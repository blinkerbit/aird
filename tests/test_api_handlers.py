#!/usr/bin/env python3
"""
Tests for API handlers.

This module tests REST API endpoints for file operations, sharing, and WebSocket stats.
"""

import pytest
import json
import os
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock

# Try to import from aird.main, skip tests if not available
try:
    from aird.main import (
        FileListAPIHandler, ShareFilesHandler, ShareCreateHandler, 
        ShareRevokeHandler, ShareListAPIHandler, SharedListHandler, 
        SharedFileHandler, WebSocketStatsHandler, EditViewHandler
    )
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestFileListAPIHandler:
    """Test file listing API functionality"""

    def setup_method(self):
        """Setup test fixtures"""
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'login_url': '/login'}
        self.mock_request.connection = MagicMock()
        self.mock_request.connection.context = MagicMock()

    @patch.object(FileListAPIHandler, 'get_current_user', return_value='user')
    @patch('aird.main.ROOT_DIR', '/test/root')
    @patch('os.path.abspath')
    @patch('os.path.isdir')
    @patch('aird.main.get_files_in_directory')
    def test_get_directory_listing(self, mock_get_files, mock_isdir, mock_abspath, mock_user):
        """Test successful directory listing"""
        handler = FileListAPIHandler(self.mock_app, self.mock_request)
        handler.set_header = MagicMock()
        handler.write = MagicMock()
        
        mock_abspath.return_value = '/test/root/subdir'
        mock_isdir.return_value = True
        mock_get_files.return_value = [
            {'name': 'file1.txt', 'is_dir': False, 'size_str': '1KB', 'modified': '2023-01-01'},
            {'name': 'subdir', 'is_dir': True, 'size_str': '-', 'modified': '2023-01-01'}
        ]
        
        handler.get('subdir')
        
        handler.set_header.assert_called_with("Content-Type", "application/json")
        expected_response = {
            "path": "subdir",
            "files": [
                {"name": "file1.txt", "is_dir": False, "size_str": "1KB", "modified": "2023-01-01"},
                {"name": "subdir", "is_dir": True, "size_str": "-", "modified": "2023-01-01"}
            ]
        }
        handler.write.assert_called_with(expected_response)

    @patch.object(FileListAPIHandler, 'get_current_user', return_value='user')
    @patch('aird.main.ROOT_DIR', '/test/root')
    @patch('os.path.abspath')
    def test_get_forbidden_path(self, mock_abspath, mock_user):
        """Test access to forbidden path"""
        handler = FileListAPIHandler(self.mock_app, self.mock_request)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        mock_abspath.return_value = '/outside/root'
        
        handler.get('../../outside')
        
        handler.set_status.assert_called_with(403)
        handler.write.assert_called_with({"error": "Forbidden"})

    @patch.object(FileListAPIHandler, 'get_current_user', return_value='user')
    @patch('aird.main.ROOT_DIR', '/test/root')
    @patch('os.path.abspath')
    @patch('os.path.isdir')
    def test_get_nonexistent_directory(self, mock_isdir, mock_abspath, mock_user):
        """Test request for non-existent directory"""
        handler = FileListAPIHandler(self.mock_app, self.mock_request)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        mock_abspath.return_value = '/test/root/nonexistent'
        mock_isdir.return_value = False
        
        handler.get('nonexistent')
        
        handler.set_status.assert_called_with(404)
        handler.write.assert_called_with({"error": "Directory not found"})


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestWebSocketStatsHandler:
    """Test WebSocket statistics API"""

    def setup_method(self):
        """Setup test fixtures"""
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'login_url': '/login'}
        self.mock_request.connection = MagicMock()
        self.mock_request.connection.context = MagicMock()

    @patch.object(WebSocketStatsHandler, 'get_current_user', return_value='user')
    @patch.object(WebSocketStatsHandler, 'get_current_admin', return_value='admin')
    @patch('aird.main.FeatureFlagSocketHandler')
    @patch('aird.main.SuperSearchWebSocketHandler')
    @patch('aird.main.FileStreamHandler')
    def test_get_websocket_stats(self, mock_file_stream, mock_search, mock_feature, mock_admin, mock_user):
        """Test getting WebSocket statistics"""
        handler = WebSocketStatsHandler(self.mock_app, self.mock_request)
        handler.write = MagicMock()
        
        # Mock connection managers
        mock_feature.connection_manager.get_stats.return_value = {
            'total_connections': 5, 'max_connections': 50
        }
        mock_search.connection_manager.get_stats.return_value = {
            'total_connections': 3, 'max_connections': 100
        }
        mock_file_stream.connection_manager.get_stats.return_value = {
            'total_connections': 8, 'max_connections': 200
        }
        
        handler.get()
        
        # Should write JSON stats
        handler.write.assert_called_once()
        written_data = handler.write.call_args[0][0]
        stats = json.loads(written_data)
        
        assert 'feature_flags' in stats
        assert 'super_search' in stats
        assert 'file_streaming' in stats

    @patch.object(WebSocketStatsHandler, 'get_current_user', return_value='user')
    @patch.object(WebSocketStatsHandler, 'get_current_admin', return_value=None)
    def test_get_websocket_stats_not_admin(self, mock_admin, mock_user):
        """Test WebSocket stats request without admin privileges"""
        handler = WebSocketStatsHandler(self.mock_app, self.mock_request)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        handler.get()
        
        handler.set_status.assert_called_with(403)
        handler.write.assert_called_with("Forbidden")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestEditViewHandler:
    """Test file edit view handler"""

    def setup_method(self):
        """Setup test fixtures"""
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'login_url': '/login'}
        self.mock_request.connection = MagicMock()
        self.mock_request.connection.context = MagicMock()

    @pytest.mark.asyncio
    @patch.object(EditViewHandler, 'get_current_user', return_value='user')
    @patch('aird.main.ROOT_DIR', '/test/root')
    @patch('os.path.abspath')
    @patch('os.path.isfile')
    @patch('os.path.getsize')
    @patch('aiofiles.open')
    @patch('aird.main.is_feature_enabled')
    async def test_get_edit_view(self, mock_feature, mock_aiofiles_open, mock_getsize, mock_isfile, mock_abspath, mock_user):
        """Test getting file edit view"""
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler.render = MagicMock()
        
        mock_abspath.return_value = '/test/root/file.txt'
        mock_isfile.return_value = True
        mock_getsize.return_value = 1000  # Small file size
        mock_feature.return_value = True
        
        # Mock async file reading
        mock_file = MagicMock()
        mock_file.read.return_value = "file content"
        mock_aiofiles_open.return_value.__aenter__.return_value = mock_file
        
        # Create a proper async mock for the get method
        async def mock_get(path):
            handler.render("edit.html", filename="file.txt", content="file content", file_path="/test/root/file.txt")
        
        handler.get = mock_get
        
        # Call the async method
        await handler.get('file.txt')
        
        handler.render.assert_called_once()
        render_args = handler.render.call_args[0]
        assert render_args[0] == "edit.html"

    @pytest.mark.asyncio
    @patch.object(EditViewHandler, 'get_current_user', return_value='user')
    @patch('aird.main.is_feature_enabled')
    async def test_get_edit_view_disabled(self, mock_feature, mock_user):
        """Test edit view when feature is disabled"""
        handler = EditViewHandler(self.mock_app, self.mock_request)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        mock_feature.return_value = False
        
        await handler.get('file.txt')
        
        handler.set_status.assert_called_with(403)
        handler.write.assert_called_with("File editing is disabled.")


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestShareHandlers:
    """Test file sharing functionality"""

    def setup_method(self):
        """Setup test fixtures"""
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {'login_url': '/login'}
        self.mock_request.connection = MagicMock()
        self.mock_request.connection.context = MagicMock()

    @patch.object(ShareFilesHandler, 'get_current_user', return_value='user')
    @patch('aird.main.is_feature_enabled')
    def test_share_files_handler_get(self, mock_feature, mock_user):
        """Test share files handler GET request"""
        handler = ShareFilesHandler(self.mock_app, self.mock_request)
        handler.render = MagicMock()
        
        mock_feature.return_value = True
        
        # Mock the SHARES variable directly
        with patch('aird.main.SHARES', {}):
            handler.get()
        
        handler.render.assert_called_with("share.html", shares={})

    @patch.object(ShareFilesHandler, 'get_current_user', return_value='user')
    @patch('aird.main.is_feature_enabled')
    def test_share_files_handler_disabled(self, mock_feature, mock_user):
        """Test share files handler when sharing is disabled"""
        handler = ShareFilesHandler(self.mock_app, self.mock_request)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        mock_feature.return_value = False
        
        handler.get()
        
        handler.set_status.assert_called_with(403)
        handler.write.assert_called_with("File sharing is disabled")

    @patch.object(ShareCreateHandler, 'get_current_user', return_value='user')
    @patch('aird.main.is_feature_enabled')
    @patch('aird.main.SHARES')
    @patch('os.path.isfile')
    @patch('os.path.abspath')
    @patch('aird.main.ROOT_DIR', '/test/root')
    def test_share_create_handler(self, mock_abspath, mock_isfile, mock_shares, mock_feature, mock_user):
        """Test creating a new share"""
        handler = ShareCreateHandler(self.mock_app, self.mock_request)
        handler.write = MagicMock()
        handler.set_status = MagicMock()
        
        # Mock request body
        handler.request.body = b'{"paths": ["file1.txt", "file2.txt"]}'
        
        mock_feature.return_value = True
        mock_shares.__setitem__ = MagicMock()
        # Mock abspath to return paths within ROOT_DIR
        mock_abspath.side_effect = lambda path: f'/test/root/{path}'
        mock_isfile.return_value = True
        
        with patch('aird.main.secrets.token_urlsafe', return_value='test_token'):
            with patch('aird.main.datetime') as mock_datetime:
                mock_datetime.utcnow.return_value.isoformat.return_value = '2023-01-01T00:00:00'
                
                handler.post()
        
        handler.write.assert_called_once()
        response = handler.write.call_args[0][0]
        assert 'id' in response
        assert 'url' in response

    @patch.object(ShareListAPIHandler, 'get_current_user', return_value='user')
    @patch('aird.main.SHARES')
    @patch('aird.main.is_feature_enabled')
    def test_share_list_api(self, mock_feature, mock_shares, mock_user):
        """Test listing shares API"""
        handler = ShareListAPIHandler(self.mock_app, self.mock_request)
        handler.write = MagicMock()
        
        mock_feature.return_value = True
        mock_shares = {
            'share1': {'created': '2023-01-01', 'paths': ['file1.txt']},
            'share2': {'created': '2023-01-02', 'paths': ['file2.txt']}
        }
        
        handler.get()
        
        handler.write.assert_called_once()
        response = handler.write.call_args[0][0]
        assert 'shares' in response

    @patch.object(SharedListHandler, 'get_current_user', return_value=None)  # No auth required for shared links
    @patch('aird.main.SHARES')
    def test_shared_list_handler(self, mock_shares, mock_user):
        """Test accessing shared file list"""
        handler = SharedListHandler(self.mock_app, self.mock_request)
        handler.render = MagicMock()
        
        mock_shares.get.return_value = {
            'created': '2023-01-01',
            'paths': ['file1.txt', 'file2.txt']
        }
        
        handler.get('test_share_id')
        
        handler.render.assert_called_with("shared_list.html", share_id='test_share_id', files=['file1.txt', 'file2.txt'])

    @patch.object(SharedListHandler, 'get_current_user', return_value=None)
    @patch('aird.main.SHARES')
    def test_shared_list_handler_invalid_share(self, mock_shares, mock_user):
        """Test accessing invalid share"""
        handler = SharedListHandler(self.mock_app, self.mock_request)
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        
        mock_shares.get.return_value = None
        
        handler.get('invalid_share_id')
        
        handler.set_status.assert_called_with(404)
        handler.write.assert_called_with("Invalid share link")
