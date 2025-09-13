#!/usr/bin/env python3
"""
Tests for file streaming functionality.

This module tests WebSocket-based file streaming capabilities.
"""

import pytest
import json
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

# Try to import from aird.main, skip tests if not available
try:
    from aird.main import FileStreamHandler
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


class MockWebSocketRequest:
    """Mock WebSocket request for testing"""
    
    def __init__(self, host="localhost:8000"):
        self.host = host
        self.connection = MagicMock()
        self.headers = {}
        self.arguments = {}
        self.query_arguments = {}


class MockWebSocketConnection:
    """Mock WebSocket connection for testing"""
    
    def __init__(self):
        self.messages = []
        self.closed = False
        self.close_code = None
        self.close_reason = None
    
    def write_message(self, message):
        if not self.closed:
            self.messages.append(message)
    
    def close(self, code=None, reason=None):
        self.closed = True
        self.close_code = code
        self.close_reason = reason


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestFileStreamHandler:
    """Test file streaming WebSocket handler"""

    def setup_method(self):
        """Setup test fixtures"""
        self.mock_app = MagicMock(ui_methods={}, ui_modules={})
        self.mock_request = MockWebSocketRequest()

    def test_check_origin_same_host(self):
        """Test origin checking for same host"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        
        # Same host should be allowed
        assert handler.check_origin("http://localhost:8000") is True
        assert handler.check_origin("https://localhost:8000") is True

    def test_check_origin_different_host(self):
        """Test origin checking for different host"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        
        # Different host should be rejected
        assert handler.check_origin("http://evil.com") is False
        assert handler.check_origin("https://attacker.net") is False

    def test_check_origin_localhost_variants(self):
        """Test origin checking for localhost variants"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        
        # Standard localhost variants should be allowed
        assert handler.check_origin("http://localhost") is True
        assert handler.check_origin("http://127.0.0.1") is True

    @pytest.mark.asyncio
    @patch.object(FileStreamHandler, 'get_current_user', return_value='user')
    async def test_open_authenticated_user(self, mock_user):
        """Test WebSocket open with authenticated user"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.close = MagicMock()
        
        # Mock connection manager
        handler.connection_manager = MagicMock()
        handler.connection_manager.add_connection.return_value = True
        
        # Mock file operations to avoid actual file I/O
        with patch('os.path.isfile', return_value=True), \
             patch('aiofiles.open'), \
             patch('builtins.open'), \
             patch('tornado.ioloop.IOLoop.current'), \
             patch('tornado.ioloop.PeriodicCallback'):
            await handler.open('test_file.txt')
        
        handler.connection_manager.add_connection.assert_called_with(handler)
        handler.close.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(FileStreamHandler, 'get_current_user', return_value=None)
    async def test_open_unauthenticated_user(self, mock_user):
        """Test WebSocket open with unauthenticated user"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.close = MagicMock()
        
        await handler.open('test_file.txt')
        
        handler.close.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(FileStreamHandler, 'get_current_user', return_value='user')
    async def test_open_connection_limit_exceeded(self, mock_user):
        """Test WebSocket open when connection limit is exceeded"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.write_message = MagicMock()
        handler.close = MagicMock()
        
        # Mock connection manager that rejects connection
        handler.connection_manager = MagicMock()
        handler.connection_manager.add_connection.return_value = False
        
        await handler.open('test_file.txt')
        
        handler.write_message.assert_called_once()
        written_message = handler.write_message.call_args[0][0]
        assert 'limit exceeded' in written_message.lower()

    @patch.object(FileStreamHandler, 'get_current_user', return_value='user')
    @patch('aird.main.ROOT_DIR', '/test/root')
    @patch('os.path.abspath')
    @patch('os.path.isfile')
    async def test_on_message_valid_file_request(self, mock_isfile, mock_abspath, mock_user):
        """Test handling valid file stream request"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.write_message = MagicMock()
        handler.connection_manager = MagicMock()
        
        mock_abspath.return_value = '/test/root/file.txt'
        mock_isfile.return_value = True
        
        message = json.dumps({
            'action': 'stream_file',
            'file_path': 'file.txt',
            'start_line': 1,
            'end_line': 10
        })
        
        with patch('aird.main.MMapFileHandler.serve_file_chunk') as mock_serve:
            mock_serve.return_value = AsyncMock()
            mock_serve.return_value.__aiter__ = AsyncMock(return_value=iter([b"line 1\n", b"line 2\n"]))
            
            await handler.on_message(message)
        
        # Should call the file serving function
        mock_serve.assert_called_once()

    @patch.object(FileStreamHandler, 'get_current_user', return_value='user')
    async def test_on_message_invalid_json(self, mock_user):
        """Test handling invalid JSON message"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.write_message = MagicMock()
        handler.connection_manager = MagicMock()
        
        await handler.on_message("invalid json")
        
        handler.write_message.assert_called_once()
        written_message = json.loads(handler.write_message.call_args[0][0])
        assert written_message['type'] == 'error'
        assert 'invalid json' in written_message['message'].lower()

    @patch.object(FileStreamHandler, 'get_current_user', return_value='user')
    @patch('aird.main.ROOT_DIR', '/test/root')
    @patch('os.path.abspath')
    async def test_on_message_forbidden_path(self, mock_abspath, mock_user):
        """Test handling request for forbidden path"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.write_message = MagicMock()
        handler.connection_manager = MagicMock()
        
        mock_abspath.return_value = '/outside/root/file.txt'
        
        message = json.dumps({
            'action': 'stream_file',
            'file_path': '../../../outside/root/file.txt'
        })
        
        await handler.on_message(message)
        
        handler.write_message.assert_called_once()
        written_message = json.loads(handler.write_message.call_args[0][0])
        assert written_message['type'] == 'error'
        assert 'forbidden' in written_message['message'].lower() or 'access denied' in written_message['message'].lower()

    @patch.object(FileStreamHandler, 'get_current_user', return_value='user')
    @patch('aird.main.ROOT_DIR', '/test/root')
    @patch('os.path.abspath')
    @patch('os.path.isfile')
    async def test_on_message_nonexistent_file(self, mock_isfile, mock_abspath, mock_user):
        """Test handling request for non-existent file"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.write_message = MagicMock()
        handler.connection_manager = MagicMock()
        
        mock_abspath.return_value = '/test/root/nonexistent.txt'
        mock_isfile.return_value = False
        
        message = json.dumps({
            'action': 'stream_file',
            'file_path': 'nonexistent.txt'
        })
        
        await handler.on_message(message)
        
        handler.write_message.assert_called_once()
        written_message = json.loads(handler.write_message.call_args[0][0])
        assert written_message['type'] == 'error'
        assert 'not found' in written_message['message'].lower()

    def test_on_close(self):
        """Test WebSocket close handling"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.connection_manager = MagicMock()
        
        # Mock file attribute
        mock_file = MagicMock()
        handler.file = mock_file
        
        handler.on_close()
        
        handler.connection_manager.remove_connection.assert_called_with(handler)
        mock_file.close.assert_called_once()

    def test_on_close_no_file(self):
        """Test WebSocket close handling when no file is open"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.connection_manager = MagicMock()
        
        # No file attribute
        
        handler.on_close()
        
        handler.connection_manager.remove_connection.assert_called_with(handler)
        # Should not raise error even without file

    @patch.object(FileStreamHandler, 'get_current_user', return_value='user')
    async def test_on_message_missing_action(self, mock_user):
        """Test handling message without action field"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.write_message = MagicMock()
        handler.connection_manager = MagicMock()
        
        message = json.dumps({
            'file_path': 'file.txt'
            # Missing 'action' field
        })
        
        await handler.on_message(message)
        
        handler.write_message.assert_called_once()
        written_message = json.loads(handler.write_message.call_args[0][0])
        assert written_message['type'] == 'error'

    @patch.object(FileStreamHandler, 'get_current_user', return_value='user')
    async def test_on_message_unknown_action(self, mock_user):
        """Test handling message with unknown action"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.write_message = MagicMock()
        handler.connection_manager = MagicMock()
        
        message = json.dumps({
            'action': 'unknown_action',
            'file_path': 'file.txt'
        })
        
        await handler.on_message(message)
        
        handler.write_message.assert_called_once()
        written_message = json.loads(handler.write_message.call_args[0][0])
        assert written_message['type'] == 'error'
        assert 'unknown' in written_message['message'].lower() or 'invalid' in written_message['message'].lower()

    def test_write_message_with_activity_update(self):
        """Test that write_message calls parent method"""
        handler = FileStreamHandler(self.mock_app, self.mock_request)
        handler.connection_manager = MagicMock()
        
        # Mock the parent write_message method
        with patch('tornado.websocket.WebSocketHandler.write_message') as mock_parent:
            mock_parent.return_value = None
            
            # Call the write_message method (inherited from parent)
            handler.write_message("test message")
            
            mock_parent.assert_called_with("test message")
