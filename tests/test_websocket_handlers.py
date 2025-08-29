"""
Unit tests for WebSocket handlers in aird.main module.
"""

import os
import json
import tempfile
import shutil
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from aird.main import (
    FeatureFlagSocketHandler,
    SuperSearchWebSocketHandler,
    FEATURE_FLAGS,
    ROOT_DIR
)


class MockWebSocketRequest:
    """Mock WebSocket request for testing"""
    
    def __init__(self, host="localhost:8000"):
        self.host = host


class MockWebSocketConnection:
    """Mock WebSocket connection for testing"""
    
    def __init__(self):
        self.messages_sent = []
        self.closed = False
        self.request = MockWebSocketRequest()
    
    def write_message(self, message):
        """Mock write_message method"""
        self.messages_sent.append(message)
    
    def close(self):
        """Mock close method"""
        self.closed = True
    
    def get_secure_cookie(self, name):
        """Mock get_secure_cookie method"""
        if name == "user":
            return "test_user"
        elif name == "admin":
            return "test_admin"
        return None


class TestFeatureFlagSocketHandler:
    """Test FeatureFlagSocketHandler class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        # Clear connections before each test
        FeatureFlagSocketHandler.connections.clear()
    
    def teardown_method(self):
        """Clean up after each test"""
        FeatureFlagSocketHandler.connections.clear()
    
    def test_check_origin_allowed(self):
        """Test that allowed origins are accepted"""
        handler = FeatureFlagSocketHandler(None, MockWebSocketRequest())
        
        allowed_origins = [
            "http://localhost:8000",
            "https://localhost:8000",
            "http://127.0.0.1:8000"
        ]
        
        for origin in allowed_origins:
            assert handler.check_origin(origin) is True
    
    def test_check_origin_denied(self):
        """Test that disallowed origins are rejected"""
        handler = FeatureFlagSocketHandler(None, MockWebSocketRequest())
        
        disallowed_origins = [
            "http://evil.com",
            "https://malicious.org",
            "http://localhost:9000"
        ]
        
        for origin in disallowed_origins:
            assert handler.check_origin(origin) is False
    
    @patch('aird.main.DB_CONN', None)
    @patch('aird.main.FEATURE_FLAGS', {'test_flag': True, 'another_flag': False})
    def test_open_adds_connection_and_sends_flags(self):
        """Test that open adds connection and sends current flags"""
        handler = FeatureFlagSocketHandler(None, MockWebSocketRequest())
        handler.write_message = MagicMock()
        
        handler.open()
        
        # Should be added to connections
        assert handler in FeatureFlagSocketHandler.connections
        
        # Should send current flags
        handler.write_message.assert_called_once()
        sent_message = handler.write_message.call_args[0][0]
        sent_flags = json.loads(sent_message)
        assert sent_flags['test_flag'] is True
        assert sent_flags['another_flag'] is False
    
    def test_on_close_removes_connection(self):
        """Test that on_close removes connection"""
        handler = FeatureFlagSocketHandler(None, MockWebSocketRequest())
        
        # Add to connections
        FeatureFlagSocketHandler.connections.add(handler)
        assert handler in FeatureFlagSocketHandler.connections
        
        # Close should remove
        handler.on_close()
        assert handler not in FeatureFlagSocketHandler.connections
    
    @patch('aird.main.DB_CONN', None)
    @patch('aird.main.FEATURE_FLAGS', {'flag1': True, 'flag2': False})
    def test_get_current_feature_flags_no_db(self):
        """Test _get_current_feature_flags without database"""
        handler = FeatureFlagSocketHandler(None, MockWebSocketRequest())
        
        flags = handler._get_current_feature_flags()
        
        assert flags == {'flag1': True, 'flag2': False}
    
    @patch('aird.main.DB_CONN')
    @patch('aird.main._load_feature_flags')
    @patch('aird.main.FEATURE_FLAGS', {'flag1': True, 'flag2': False})
    def test_get_current_feature_flags_with_db(self, mock_load_flags, mock_db):
        """Test _get_current_feature_flags with database"""
        mock_load_flags.return_value = {'flag1': False, 'flag3': True}
        
        handler = FeatureFlagSocketHandler(None, MockWebSocketRequest())
        flags = handler._get_current_feature_flags()
        
        # Should merge database flags with runtime flags
        assert flags['flag1'] is True  # Runtime overrides database
        assert flags['flag2'] is False  # From runtime
        assert flags['flag3'] is True  # From database
    
    @patch('aird.main.DB_CONN', None)
    @patch('aird.main.FEATURE_FLAGS', {'test_flag': True})
    def test_send_updates_to_connections(self):
        """Test send_updates sends to all connections"""
        # Create mock connections
        conn1 = MagicMock()
        conn2 = MagicMock()
        
        FeatureFlagSocketHandler.connections.add(conn1)
        FeatureFlagSocketHandler.connections.add(conn2)
        
        FeatureFlagSocketHandler.send_updates()
        
        # Both connections should receive the message
        conn1.write_message.assert_called_once()
        conn2.write_message.assert_called_once()
        
        # Check message content
        sent_message = conn1.write_message.call_args[0][0]
        sent_flags = json.loads(sent_message)
        assert sent_flags['test_flag'] is True
    
    def test_send_updates_handles_dead_connections(self):
        """Test send_updates removes dead connections"""
        # Create mock connections, one that raises exception
        good_conn = MagicMock()
        bad_conn = MagicMock()
        bad_conn.write_message.side_effect = Exception("Connection dead")
        
        FeatureFlagSocketHandler.connections.add(good_conn)
        FeatureFlagSocketHandler.connections.add(bad_conn)
        
        FeatureFlagSocketHandler.send_updates()
        
        # Good connection should receive message
        good_conn.write_message.assert_called_once()
        
        # Bad connection should be removed from connections
        assert bad_conn not in FeatureFlagSocketHandler.connections
        assert good_conn in FeatureFlagSocketHandler.connections


class TestSuperSearchWebSocketHandler:
    """Test SuperSearchWebSocketHandler class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.original_root = ROOT_DIR
        
        # Patch ROOT_DIR for testing
        self.root_patcher = patch('aird.main.ROOT_DIR', self.temp_dir)
        self.root_patcher.start()
    
    def teardown_method(self):
        """Clean up test fixtures"""
        self.root_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_check_origin_allowed(self):
        """Test that allowed origins are accepted"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        
        allowed_origins = [
            "http://localhost:8000",
            "https://localhost:8000",
            "http://127.0.0.1:8000"
        ]
        
        for origin in allowed_origins:
            assert handler.check_origin(origin) is True
    
    def test_check_origin_denied(self):
        """Test that disallowed origins are rejected"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        
        disallowed_origins = [
            "http://evil.com",
            "https://malicious.org",
            "http://localhost:9000"
        ]
        
        for origin in disallowed_origins:
            assert handler.check_origin(origin) is False
    
    def test_open_with_authenticated_user(self):
        """Test open with authenticated user"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.current_user = "test_user"
        handler.close = MagicMock()
        
        handler.open()
        
        assert handler.search_cancelled is False
        handler.close.assert_not_called()
    
    def test_open_without_authenticated_user(self):
        """Test open without authenticated user"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.current_user = None
        handler.close = MagicMock()
        
        handler.open()
        
        handler.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_on_message_invalid_json(self):
        """Test on_message with invalid JSON"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        handler.search_cancelled = False
        
        await handler.on_message("invalid json")
        
        handler.write_message.assert_called_once()
        sent_message = handler.write_message.call_args[0][0]
        message_data = json.loads(sent_message)
        assert message_data['type'] == 'error'
        assert 'Invalid JSON format' in message_data['message']
    
    @pytest.mark.asyncio
    async def test_on_message_missing_parameters(self):
        """Test on_message with missing required parameters"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        handler.search_cancelled = False
        
        # Missing search_text
        message = json.dumps({"pattern": "*.py"})
        await handler.on_message(message)
        
        handler.write_message.assert_called_once()
        sent_message = handler.write_message.call_args[0][0]
        message_data = json.loads(sent_message)
        assert message_data['type'] == 'error'
        assert 'Both pattern and search text are required' in message_data['message']
    
    @pytest.mark.asyncio
    async def test_on_message_search_cancelled(self):
        """Test on_message when search is cancelled"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        handler.search_cancelled = True
        
        message = json.dumps({"pattern": "*.py", "search_text": "test"})
        await handler.on_message(message)
        
        # Should not call write_message when cancelled
        handler.write_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_perform_search_no_files_found(self):
        """Test perform_search when no files match pattern"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        handler.search_cancelled = False
        
        await handler.perform_search("*.nonexistent", "test")
        
        # Should send search_start and no_files messages
        assert handler.write_message.call_count >= 2
        
        # Check for no_files message
        messages = [json.loads(call.args[0]) for call in handler.write_message.call_args_list]
        no_files_msg = next((msg for msg in messages if msg['type'] == 'no_files'), None)
        assert no_files_msg is not None
        assert 'No files found matching pattern' in no_files_msg['message']
    
    @pytest.mark.asyncio
    async def test_perform_search_with_files(self):
        """Test perform_search with matching files"""
        # Create test files
        test_file1 = os.path.join(self.temp_dir, "test1.py")
        test_file2 = os.path.join(self.temp_dir, "test2.py")
        
        with open(test_file1, "w") as f:
            f.write("def test_function():\n    return 'hello world'\n")
        
        with open(test_file2, "w") as f:
            f.write("print('no match here')\n")
        
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        handler.search_cancelled = False
        
        await handler.perform_search("*.py", "hello")
        
        # Should send multiple messages including search_start, file_start, match, file_end, search_complete
        assert handler.write_message.call_count >= 4
        
        # Check for match message
        messages = [json.loads(call.args[0]) for call in handler.write_message.call_args_list]
        match_msg = next((msg for msg in messages if msg['type'] == 'match'), None)
        assert match_msg is not None
        assert match_msg['search_text'] == 'hello'
        assert 'hello world' in match_msg['line_content']
    
    @pytest.mark.asyncio
    async def test_search_traditional_method(self):
        """Test search_traditional method"""
        # Create test file
        test_file = os.path.join(self.temp_dir, "small.txt")
        with open(test_file, "w") as f:
            f.write("line 1 with search term\nline 2 without match\nline 3 with search term\n")
        
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        handler.search_cancelled = False
        
        await handler.search_traditional("small.txt", test_file, "search term")
        
        # Should send match messages for lines containing search term
        assert handler.write_message.call_count == 2  # Two matches
        
        messages = [json.loads(call.args[0]) for call in handler.write_message.call_args_list]
        assert all(msg['type'] == 'match' for msg in messages)
        assert messages[0]['line_number'] == 1
        assert messages[1]['line_number'] == 3
    
    @pytest.mark.asyncio
    async def test_send_match(self):
        """Test send_match method"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        
        await handler.send_match(
            "test.py", 
            42, 
            "This is a test line with test words", 
            "test"
        )
        
        handler.write_message.assert_called_once()
        sent_message = handler.write_message.call_args[0][0]
        message_data = json.loads(sent_message)
        
        assert message_data['type'] == 'match'
        assert message_data['file_path'] == 'test.py'
        assert message_data['line_number'] == 42
        assert message_data['line_content'] == 'This is a test line with test words'
        assert message_data['search_text'] == 'test'
        # Should find two occurrences of 'test' at positions 10 and 30
        assert len(message_data['match_positions']) == 2
        assert 10 in message_data['match_positions']
        assert 30 in message_data['match_positions']
    
    def test_on_close_cancels_search(self):
        """Test that on_close cancels search"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.search_cancelled = False
        
        handler.on_close()
        
        assert handler.search_cancelled is True
    
    @pytest.mark.asyncio
    async def test_search_with_forbidden_path(self):
        """Test search with path outside root directory"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        handler.search_cancelled = False
        
        # Try to search outside root directory
        await handler.perform_search("/etc/*", "password")
        
        # Should send error message
        handler.write_message.assert_called()
        messages = [json.loads(call.args[0]) for call in handler.write_message.call_args_list]
        error_msg = next((msg for msg in messages if msg['type'] == 'error'), None)
        assert error_msg is not None
        assert 'within the server root directory' in error_msg['message']
    
    @pytest.mark.asyncio
    async def test_search_in_file_error_handling(self):
        """Test search_in_file handles file errors gracefully"""
        handler = SuperSearchWebSocketHandler(None, MockWebSocketRequest())
        handler.write_message = AsyncMock()
        handler.search_cancelled = False
        
        # Try to search in non-existent file
        await handler.search_in_file("nonexistent.txt", "/nonexistent/path", "test")
        
        # Should send file_error message
        handler.write_message.assert_called()
        sent_message = handler.write_message.call_args[0][0]
        message_data = json.loads(sent_message)
        assert message_data['type'] == 'file_error'
        assert message_data['file_path'] == 'nonexistent.txt'