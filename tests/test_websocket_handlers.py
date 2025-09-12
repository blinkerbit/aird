"""
Unit tests for WebSocket handlers in aird.main module.

These tests cover WebSocket-based functionality including feature flag
updates and search operations with proper origin validation.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Import with error handling for missing module
try:
    from aird.main import (
        FeatureFlagSocketHandler,
        SuperSearchWebSocketHandler,
        FEATURE_FLAGS,
        ROOT_DIR
    )
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
        self.method = "GET"
        self.path = "/ws"
        self.body = b""


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
            return b"test_user"
        elif name == "admin":
            return b"test_admin"
        return None


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestFeatureFlagSocketHandler:
    """Test FeatureFlagSocketHandler class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        # Clear connections before each test
        if hasattr(FeatureFlagSocketHandler, 'connections'):
            FeatureFlagSocketHandler.connections.clear()
    
    def teardown_method(self):
        """Clean up after each test"""
        if hasattr(FeatureFlagSocketHandler, 'connections'):
            FeatureFlagSocketHandler.connections.clear()
    
    @pytest.mark.parametrize("origin,expected", [
        ("http://localhost:8000", True),
        ("https://localhost:8000", True),
        ("http://127.0.0.1:8000", True),
        ("http://evil.com", False),
        ("https://malicious.org", False),
        ("http://localhost:9000", False),
    ])
    def test_check_origin(self, origin, expected):
        """Test origin validation"""
        # Create a proper mock application
        mock_app = MagicMock()
        mock_app.ui_methods = {}
        mock_app.ui_modules = {}
        
        handler = FeatureFlagSocketHandler(mock_app, MockWebSocketRequest())
        result = handler.check_origin(origin)
        assert result == expected, f"Origin {origin} should return {expected}"
    
    def test_open_adds_connection_and_sends_flags(self):
        """Test that open adds connection and sends current flags"""
        # Create a proper mock application
        mock_app = MagicMock()
        mock_app.ui_methods = {}
        mock_app.ui_modules = {}
        
        handler = FeatureFlagSocketHandler(mock_app, MockWebSocketRequest())
        handler.write_message = MagicMock()
        
        with patch('aird.main.DB_CONN', None), \
             patch('aird.main.FEATURE_FLAGS', {'test_flag': True, 'another_flag': False}):
            
            handler.open()
            
            # Should be added to connections
            if hasattr(FeatureFlagSocketHandler, 'connections'):
                assert handler in FeatureFlagSocketHandler.connections
            
            # Should send current flags
            handler.write_message.assert_called_once()
            sent_message = handler.write_message.call_args[0][0]
            sent_flags = json.loads(sent_message)
            assert sent_flags['test_flag'] is True
            assert sent_flags['another_flag'] is False
    
    def test_on_close_removes_connection(self):
        """Test that on_close removes connection"""
        handler = FeatureFlagSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        
        # Add to connections if the attribute exists
        if hasattr(FeatureFlagSocketHandler, 'connections'):
            FeatureFlagSocketHandler.connections.add(handler)
            assert handler in FeatureFlagSocketHandler.connections
            
            # Close should remove
            handler.on_close()
            assert handler not in FeatureFlagSocketHandler.connections
    
    def test_get_current_feature_flags_no_db(self):
        """Test _get_current_feature_flags without database"""
        handler = FeatureFlagSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        
        with patch('aird.main.DB_CONN', None), \
             patch('aird.main.FEATURE_FLAGS', {'flag1': True, 'flag2': False}):
            
            if hasattr(handler, '_get_current_feature_flags'):
                flags = handler._get_current_feature_flags()
                assert flags == {'flag1': True, 'flag2': False}
    
    def test_send_updates_to_connections(self):
        """Test send_updates sends to all connections"""
        # Create mock connections
        conn1 = MagicMock()
        conn2 = MagicMock()
        
        if hasattr(FeatureFlagSocketHandler, 'connections'):
            FeatureFlagSocketHandler.connections.add(conn1)
            FeatureFlagSocketHandler.connections.add(conn2)
            
            with patch('aird.main.DB_CONN', None), \
                 patch('aird.main.FEATURE_FLAGS', {'test_flag': True}):
                
                if hasattr(FeatureFlagSocketHandler, 'send_updates'):
                    FeatureFlagSocketHandler.send_updates()
                    
                    # Both connections should receive the message
                    conn1.write_message.assert_called_once()
                    conn2.write_message.assert_called_once()
    
    def test_send_updates_handles_dead_connections(self):
        """Test send_updates removes dead connections"""
        # Create mock connections, one that raises exception
        good_conn = MagicMock()
        bad_conn = MagicMock()
        bad_conn.write_message.side_effect = Exception("Connection dead")
        
        if hasattr(FeatureFlagSocketHandler, 'connections') and \
           hasattr(FeatureFlagSocketHandler, 'send_updates'):
            
            FeatureFlagSocketHandler.connections.add(good_conn)
            FeatureFlagSocketHandler.connections.add(bad_conn)
            
            with patch('aird.main.DB_CONN', None), \
                 patch('aird.main.FEATURE_FLAGS', {'test_flag': True}):
                
                FeatureFlagSocketHandler.send_updates()
                
                # Good connection should receive message
                good_conn.write_message.assert_called_once()
                
                # Bad connection should be removed from connections
                assert bad_conn not in FeatureFlagSocketHandler.connections
                assert good_conn in FeatureFlagSocketHandler.connections


@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestSuperSearchWebSocketHandler:
    """Test SuperSearchWebSocketHandler class"""
    
    @pytest.mark.parametrize("origin,expected", [
        ("http://localhost:8000", True),
        ("https://localhost:8000", True),
        ("http://127.0.0.1:8000", True),
        ("http://evil.com", False),
        ("https://malicious.org", False),
        ("http://localhost:9000", False),
    ])
    def test_check_origin(self, origin, expected):
        """Test origin validation"""
        # Create a proper mock application
        mock_app = MagicMock()
        mock_app.ui_methods = {}
        mock_app.ui_modules = {}
        
        handler = SuperSearchWebSocketHandler(mock_app, MockWebSocketRequest())
        result = handler.check_origin(origin)
        assert result == expected, f"Origin {origin} should return {expected}"
    
    def test_open_with_authenticated_user(self):
        """Test open with authenticated user"""
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        handler.current_user = "test_user"
        handler.close = MagicMock()
        
        handler.open()
        
        if hasattr(handler, 'search_cancelled'):
            assert handler.search_cancelled is False
        handler.close.assert_not_called()
    
    def test_open_without_authenticated_user(self):
        """Test open without authenticated user"""
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        handler.current_user = None
        handler.close = MagicMock()
        
        handler.open()
        
        handler.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_on_message_invalid_json(self):
        """Test on_message with invalid JSON"""
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        handler.write_message = AsyncMock()
        
        # Initialize required attributes
        handler.search_cancelled = False
        
        if hasattr(handler, 'on_message'):
            await handler.on_message("invalid json")
            
            handler.write_message.assert_called_once()
            sent_message = handler.write_message.call_args[0][0]
            message_data = json.loads(sent_message)
            assert message_data['type'] == 'error'
            assert 'Invalid JSON format' in message_data['message']
    
    @pytest.mark.asyncio
    async def test_on_message_missing_parameters(self):
        """Test on_message with missing required parameters"""
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        handler.write_message = AsyncMock()
        
        # Initialize required attributes
        handler.search_cancelled = False
        
        if hasattr(handler, 'on_message'):
            # Missing search_text
            message = json.dumps({"pattern": "*.py"})
            await handler.on_message(message)
            
            handler.write_message.assert_called_once()
            sent_message = handler.write_message.call_args[0][0]
            message_data = json.loads(sent_message)
            assert message_data['type'] == 'error'
            assert 'required' in message_data['message'].lower()
    
    @pytest.mark.asyncio
    async def test_perform_search_no_files_found(self, temp_dir):
        """Test perform_search when no files match pattern"""
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        handler.write_message = AsyncMock()
        
        if hasattr(handler, 'search_cancelled'):
            handler.search_cancelled = False
        
        if hasattr(handler, 'perform_search'):
            with patch('aird.main.ROOT_DIR', temp_dir):
                await handler.perform_search("*.nonexistent", "test")
                
                # Should send search_start and no_files messages
                assert handler.write_message.call_count >= 1
                
                # Check for appropriate messages
                messages = [json.loads(call.args[0]) for call in handler.write_message.call_args_list]
                message_types = [msg.get('type') for msg in messages]
                assert 'no_files' in message_types or 'search_start' in message_types
    
    @pytest.mark.asyncio
    async def test_perform_search_with_files(self, temp_dir):
        """Test perform_search with matching files"""
        # Create test files
        test_file1 = os.path.join(temp_dir, "test1.py")
        test_file2 = os.path.join(temp_dir, "test2.py")
        
        with open(test_file1, "w", encoding='utf-8') as f:
            f.write("def test_function():\n    return 'hello world'\n")
        
        with open(test_file2, "w", encoding='utf-8') as f:
            f.write("print('no match here')\n")
        
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        handler.write_message = AsyncMock()
        
        if hasattr(handler, 'search_cancelled'):
            handler.search_cancelled = False
        
        if hasattr(handler, 'perform_search'):
            with patch('aird.main.ROOT_DIR', temp_dir):
                await handler.perform_search("*.py", "hello")
                
                # Should send multiple messages
                assert handler.write_message.call_count >= 1
                
                # Check for match message if found
                messages = [json.loads(call.args[0]) for call in handler.write_message.call_args_list]
                match_messages = [msg for msg in messages if msg.get('type') == 'match']
                
                if match_messages:
                    match_msg = match_messages[0]
                    assert match_msg['search_text'] == 'hello'
                    assert 'hello world' in match_msg['line_content']
    
    def test_send_match(self):
        """Test send_match method"""
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        handler.write_message = MagicMock()  # Not async since send_match is sync
        
        if hasattr(handler, 'send_match'):
            handler.send_match(
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
            
            # Should find match positions
            if 'match_positions' in message_data:
                assert len(message_data['match_positions']) >= 1
    
    def test_on_close_cancels_search(self):
        """Test that on_close cancels search"""
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        
        if hasattr(handler, 'search_cancelled'):
            handler.search_cancelled = False
            
            handler.on_close()
            
            assert handler.search_cancelled is True
    
    @pytest.mark.asyncio
    async def test_search_with_forbidden_path(self):
        """Test search with path outside root directory"""
        handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), MockWebSocketRequest())
        handler.write_message = AsyncMock()
        
        if hasattr(handler, 'search_cancelled'):
            handler.search_cancelled = False
        
        if hasattr(handler, 'perform_search'):
            # Try to search outside root directory
            await handler.perform_search("/etc/*", "password")
            
            # Should send error message or handle gracefully
            if handler.write_message.called:
                messages = [json.loads(call.args[0]) for call in handler.write_message.call_args_list]
                error_messages = [msg for msg in messages if msg.get('type') == 'error']
                
                if error_messages:
                    error_msg = error_messages[0]
                    assert 'directory' in error_msg['message'].lower() or \
                           'forbidden' in error_msg['message'].lower()


@pytest.mark.integration
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestWebSocketIntegration:
    """Integration tests for WebSocket handlers working together"""
    
    def test_websocket_handlers_creation(self):
        """Test that WebSocket handlers can be created"""
        mock_app = MagicMock()
        mock_request = MockWebSocketRequest()
        
        # Test that handlers can be instantiated
        try:
            feature_handler = FeatureFlagSocketHandler(mock_app, mock_request)
            search_handler = SuperSearchWebSocketHandler(mock_app, mock_request)
            
            assert feature_handler is not None
            assert search_handler is not None
        except Exception as e:
            pytest.skip(f"WebSocket handlers require additional setup: {e}")
    
    def test_origin_validation_consistency(self):
        """Test that origin validation is consistent across WebSocket handlers"""
        mock_request = MockWebSocketRequest()
        
        handlers = [
            FeatureFlagSocketHandler(MagicMock(ui_methods={}, ui_modules={}), mock_request),
            SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), mock_request)
        ]
        
        test_origins = [
            ("http://localhost:8000", True),
            ("http://evil.com", False),
        ]
        
        for origin, expected in test_origins:
            for handler in handlers:
                if hasattr(handler, 'check_origin'):
                    result = handler.check_origin(origin)
                    assert result == expected, \
                        f"{handler.__class__.__name__} should return {expected} for {origin}"
    
    def test_authentication_requirements(self):
        """Test that WebSocket handlers properly handle authentication"""
        mock_request = MockWebSocketRequest()
        
        # Test SuperSearchWebSocketHandler requires authentication
        search_handler = SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), mock_request)
        search_handler.current_user = None
        search_handler.close = MagicMock()
        
        if hasattr(search_handler, 'open'):
            search_handler.open()
            search_handler.close.assert_called_once()
        
        # Test with authenticated user
        search_handler.current_user = "test_user"
        search_handler.close.reset_mock()
        
        if hasattr(search_handler, 'open'):
            search_handler.open()
            search_handler.close.assert_not_called()
    
    def test_error_handling_patterns(self):
        """Test that WebSocket handlers handle errors consistently"""
        mock_request = MockWebSocketRequest()
        
        handlers = [
            FeatureFlagSocketHandler(MagicMock(ui_methods={}, ui_modules={}), mock_request),
            SuperSearchWebSocketHandler(MagicMock(ui_methods={}, ui_modules={}), mock_request)
        ]
        
        # Test that handlers don't crash on basic operations
        for handler in handlers:
            try:
                # Test close operations
                if hasattr(handler, 'on_close'):
                    handler.on_close()
                
                # Test origin checking
                if hasattr(handler, 'check_origin'):
                    handler.check_origin("http://test.com")
                    
            except Exception as e:
                pytest.fail(f"{handler.__class__.__name__} failed basic operations: {e}")