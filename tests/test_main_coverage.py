#!/usr/bin/env python3
"""
Additional unit tests to increase coverage of aird/main.py
"""

import pytest
import os
import time
import tempfile
import json
from unittest.mock import MagicMock, patch, mock_open, call
import tornado.web
import tornado.ioloop
from aird.main import (
    WebSocketConnectionManager, FilterExpression, BaseHandler, 
    MainHandler, SuperSearchWebSocketHandler,
    FileStreamHandler, WebSocketStatsHandler, EditViewHandler,
    ShareCreateHandler, ShareFilesHandler, ShareListAPIHandler,
    SharedListHandler, LDAPLoginHandler, make_app, get_files_in_directory,
    is_video_file, is_audio_file, join_path, print_banner,
    RUST_AVAILABLE, HybridFileHandler, HybridCompressionHandler
)


class TestWebSocketConnectionManager:
    """Test WebSocketConnectionManager functionality"""
    
    def setup_method(self):
        self.manager = WebSocketConnectionManager("test")
    
    def test_get_idle_timeout_with_config(self):
        """Test getting idle timeout from config"""
        with patch('aird.main.get_current_websocket_config') as mock_config:
            mock_config.return_value = {"test_idle_timeout": 300}
            timeout = self.manager.idle_timeout
            assert timeout == 300
    
    def test_get_idle_timeout_default(self):
        """Test getting default idle timeout when config is missing"""
        with patch('aird.main.get_current_websocket_config') as mock_config:
            mock_config.return_value = {}
            timeout = self.manager.idle_timeout
            assert timeout == self.manager.default_idle_timeout
    
    def test_get_max_connections_with_config(self):
        """Test getting max connections from config"""
        with patch('aird.main.get_current_websocket_config') as mock_config:
            mock_config.return_value = {"test_max_connections": 50}
            max_conn = self.manager.max_connections
            assert max_conn == 50
    
    def test_get_max_connections_default(self):
        """Test getting default max connections when config is missing"""
        with patch('aird.main.get_current_websocket_config') as mock_config:
            mock_config.return_value = {}
            max_conn = self.manager.max_connections
            assert max_conn == self.manager.default_max_connections
    
    def test_setup_cleanup_timer(self):
        """Test cleanup timer setup"""
        with patch('tornado.ioloop.IOLoop.current') as mock_loop:
            mock_loop_instance = MagicMock()
            mock_loop.return_value = mock_loop_instance
            
            self.manager._setup_cleanup_timer()
            
            # Verify that call_later was called
            mock_loop_instance.call_later.assert_called_once()
            args = mock_loop_instance.call_later.call_args
            assert args[0][0] == 60  # 60 seconds
    
    def test_update_activity(self):
        """Test updating connection activity"""
        mock_conn = MagicMock()
        with patch('time.time', return_value=12345.0):
            self.manager.update_activity(mock_conn)
            assert self.manager.last_activity[mock_conn] == 12345.0
    
    def test_cleanup_dead_connections_with_ping(self):
        """Test cleanup of dead connections with ping"""
        mock_conn1 = MagicMock()
        mock_conn1.ws_connection = MagicMock()
        mock_conn1.ping = MagicMock()
        
        mock_conn2 = MagicMock()
        mock_conn2.ws_connection = None
        
        self.manager.connections = {mock_conn1, mock_conn2}
        
        with patch.object(self.manager, 'remove_connection') as mock_remove:
            self.manager.cleanup_dead_connections()
            
            # conn1 should ping, conn2 should be removed (no ws_connection)
            mock_conn1.ping.assert_called_once()
            mock_remove.assert_called_with(mock_conn2)
    
    def test_cleanup_dead_connections_with_exception(self):
        """Test cleanup when ping raises exception"""
        mock_conn = MagicMock()
        mock_conn.ws_connection = MagicMock()
        mock_conn.ping = MagicMock(side_effect=Exception("Connection error"))
        
        self.manager.connections = {mock_conn}
        
        with patch.object(self.manager, 'remove_connection') as mock_remove:
            self.manager.cleanup_dead_connections()
            mock_remove.assert_called_with(mock_conn)
    
    def test_cleanup_idle_connections(self):
        """Test cleanup of idle connections"""
        mock_conn1 = MagicMock()
        mock_conn2 = MagicMock()
        
        self.manager.connections = {mock_conn1, mock_conn2}
        self.manager.last_activity = {
            mock_conn1: time.time() - 1000,  # Very old
            mock_conn2: time.time() - 10     # Recent
        }
        
        with patch.object(self.manager, 'remove_connection') as mock_remove:
            with patch('aird.main.get_current_websocket_config', return_value={"test_idle_timeout": 100}):
                self.manager.cleanup_idle_connections()
                mock_remove.assert_called_with(mock_conn1)
    
    def test_add_connection(self):
        """Test adding a connection"""
        mock_conn = MagicMock()
        
        with patch('aird.main.get_current_websocket_config', return_value={"test_max_connections": 10}):
            with patch.object(self.manager, 'cleanup_dead_connections'):
                with patch.object(self.manager, 'cleanup_idle_connections'):
                    result = self.manager.add_connection(mock_conn)
                    assert result is True
                    assert mock_conn in self.manager.connections
    
    def test_add_connection_at_limit(self):
        """Test adding a connection when at limit"""
        mock_conn = MagicMock()
        
        # Fill up connections to the limit
        for i in range(10):
            self.manager.connections.add(MagicMock())
        
        with patch('aird.main.get_current_websocket_config', return_value={"test_max_connections": 10}):
            with patch.object(self.manager, 'cleanup_dead_connections'):
                with patch.object(self.manager, 'cleanup_idle_connections'):
                    result = self.manager.add_connection(mock_conn)
                    assert result is False
                    assert mock_conn not in self.manager.connections
    
    def test_remove_connection(self):
        """Test removing a connection"""
        mock_conn = MagicMock()
        self.manager.connections.add(mock_conn)
        self.manager.last_activity[mock_conn] = time.time()
        self.manager.connection_times[mock_conn] = time.time()
        
        self.manager.remove_connection(mock_conn)
        
        assert mock_conn not in self.manager.connections
        assert mock_conn not in self.manager.last_activity
        assert mock_conn not in self.manager.connection_times


class TestUtilityFunctions:
    """Test utility functions for better coverage"""
    
    def test_join_path_edge_cases(self):
        """Test join_path with edge cases"""
        # Test with empty parts
        assert join_path("", "file.txt") == "file.txt"
        assert join_path("dir", "") == "dir/"
        assert join_path("", "") == ""
        
        # Test with None (should handle gracefully)
        try:
            result = join_path(None, "file.txt")
            # Should not crash, result depends on implementation
        except (TypeError, AttributeError):
            # Expected if implementation doesn't handle None
            pass
    
    def test_is_video_file_edge_cases(self):
        """Test is_video_file with edge cases"""
        # Test with empty string
        assert is_video_file("") is False
        
        # Test with path without extension
        assert is_video_file("video") is False
        
        # Test with mixed case
        assert is_video_file("video.MP4") is True
        assert is_video_file("VIDEO.AVI") is True
        
        # Test with None - should handle gracefully
        try:
            result = is_video_file(None)
            # Should not crash
        except (TypeError, AttributeError):
            # Expected if implementation doesn't handle None
            pass
    
    def test_is_audio_file_edge_cases(self):
        """Test is_audio_file with edge cases"""
        # Test with empty string
        assert is_audio_file("") is False
        
        # Test with path without extension
        assert is_audio_file("audio") is False
        
        # Test with mixed case
        assert is_audio_file("audio.MP3") is True
        assert is_audio_file("AUDIO.WAV") is True
        
        # Test with None - should handle gracefully
        try:
            result = is_audio_file(None)
            # Should not crash
        except (TypeError, AttributeError):
            # Expected if implementation doesn't handle None
            pass
    
    def test_print_banner(self):
        """Test print_banner function"""
        with patch('builtins.print') as mock_print:
            print_banner()
            mock_print.assert_called()
            # Verify it prints something
            call_args = mock_print.call_args_list
            assert len(call_args) > 0


class TestFilterExpressionEdgeCases:
    """Test FilterExpression edge cases for better coverage"""
    
    def test_filter_expression_with_none(self):
        """Test FilterExpression with None input"""
        fe = FilterExpression(None)
        assert fe.original_expression is None
        assert fe.parsed_expression is None
        assert fe.matches("any line") is True  # Should return True when parsed_expression is None
    
    def test_filter_expression_with_empty_string(self):
        """Test FilterExpression with empty string"""
        fe = FilterExpression("")
        assert fe.original_expression == ""
        # Should handle empty string gracefully
        result = fe.matches("any line")
        assert isinstance(result, bool)
    
    def test_filter_expression_with_whitespace_only(self):
        """Test FilterExpression with whitespace only"""
        fe = FilterExpression("   \t\n   ")
        assert fe.original_expression == "   \t\n   "
        result = fe.matches("any line")
        assert isinstance(result, bool)
    
    def test_filter_expression_complex_parsing_errors(self):
        """Test FilterExpression with complex expressions that might fail parsing"""
        # Test malformed expressions
        malformed_expressions = [
            "((unclosed parenthesis",
            "AND without left operand",
            "OR without left operand",
            "()",  # Empty parentheses
            "((()))",  # Nested empty parentheses
        ]
        
        for expr in malformed_expressions:
            fe = FilterExpression(expr)
            # Should not crash and should return a boolean
            result = fe.matches("test line")
            assert isinstance(result, bool)
    
    def test_filter_expression_str_representation(self):
        """Test FilterExpression string representation"""
        fe = FilterExpression("test expression")
        str_repr = str(fe)
        assert "FilterExpression" in str_repr
        assert "test expression" in str_repr


class TestMakeApp:
    """Test make_app function for better coverage"""
    
    def test_make_app_with_custom_settings(self):
        """Test make_app with custom settings"""
        custom_settings = {
            'cookie_secret': 'test_secret',
            'template_path': 'custom_templates',
            'debug': True,
            'autoreload': True
        }
        
        with patch('os.path.join', return_value='custom_templates'):
            with patch('os.path.dirname', return_value='.'):
                app = make_app(custom_settings)
                assert app is not None
                assert app.settings['debug'] is True
                assert app.settings['autoreload'] is True
    
    def test_make_app_with_minimal_settings(self):
        """Test make_app with minimal settings"""
        minimal_settings = {'cookie_secret': 'test_secret'}
        
        with patch('os.path.join', return_value='templates'):
            with patch('os.path.dirname', return_value='.'):
                app = make_app(minimal_settings)
                assert app is not None
                assert 'template_path' in app.settings
    
    def test_make_app_with_ldap_enabled(self):
        """Test make_app with LDAP enabled"""
        settings = {'cookie_secret': 'test_secret'}
        
        with patch('os.path.join', return_value='templates'):
            with patch('os.path.dirname', return_value='.'):
                app = make_app(settings, ldap_enabled=True, ldap_server='ldap://test.com', ldap_base_dn='dc=test')
                assert app is not None
                # Should have LDAP-related handlers
                handlers = app.default_router.rules
                assert len(handlers) > 0


class TestGetFilesInDirectory:
    """Test get_files_in_directory function for better coverage"""
    
    def test_get_files_in_directory_with_symlinks(self):
        """Test get_files_in_directory with symlinks"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a regular file
            regular_file = os.path.join(temp_dir, 'regular.txt')
            with open(regular_file, 'w') as f:
                f.write('content')
            
            # Create a symlink (if supported on the platform)
            try:
                symlink_file = os.path.join(temp_dir, 'symlink.txt')
                os.symlink(regular_file, symlink_file)
                
                files = get_files_in_directory(temp_dir)
                # Should include both regular file and symlink
                assert len(files) >= 1
            except OSError:
                # Symlinks not supported on this platform
                files = get_files_in_directory(temp_dir)
                assert len(files) == 1
    
    def test_get_files_in_directory_with_hidden_files(self):
        """Test get_files_in_directory with hidden files"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a regular file
            regular_file = os.path.join(temp_dir, 'regular.txt')
            with open(regular_file, 'w') as f:
                f.write('content')
            
            # Create a hidden file
            hidden_file = os.path.join(temp_dir, '.hidden.txt')
            with open(hidden_file, 'w') as f:
                f.write('hidden content')
            
            files = get_files_in_directory(temp_dir)
            # Should include both regular and hidden files
            assert len(files) == 2
    
    def test_get_files_in_directory_empty_directory(self):
        """Test get_files_in_directory with empty directory"""
        with tempfile.TemporaryDirectory() as temp_dir:
            files = get_files_in_directory(temp_dir)
            assert files == []
    
    def test_get_files_in_directory_with_subdirectories(self):
        """Test get_files_in_directory with subdirectories"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a file
            regular_file = os.path.join(temp_dir, 'file.txt')
            with open(regular_file, 'w') as f:
                f.write('content')
            
            # Create a subdirectory
            subdir = os.path.join(temp_dir, 'subdir')
            os.makedirs(subdir)
            
            files = get_files_in_directory(temp_dir)
            # Should include both file and subdirectory
            assert len(files) == 2
            
            # Check that we have both file and directory entries
            file_names = [f['name'] for f in files]
            assert 'file.txt' in file_names
            assert 'subdir' in file_names


class TestWebSocketStatsHandler:
    """Test WebSocketStatsHandler for better coverage"""
    
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.handler = WebSocketStatsHandler(self.mock_app, self.mock_request)
    
    def test_get_websocket_stats_admin(self):
        """Test WebSocketStatsHandler when user is admin"""
        handler = WebSocketStatsHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value="admin_user")
        handler.get_current_admin = MagicMock(return_value="admin_user")
        handler.write = MagicMock()
        handler.set_header = MagicMock()
        
        with patch('aird.main.FeatureFlagSocketHandler') as mock_feature:
            with patch('aird.main.FileStreamHandler') as mock_file:
                with patch('aird.main.SuperSearchWebSocketHandler') as mock_search:
                    mock_feature.connection_manager.get_stats.return_value = {'connections': 2}
                    mock_file.connection_manager.get_stats.return_value = {'connections': 3}
                    mock_search.connection_manager.get_stats.return_value = {'connections': 5}
                    
                    handler.get()
                    handler.write.assert_called_once()
                    handler.set_header.assert_called_with('Content-Type', 'application/json')
    
    def test_get_websocket_stats_not_admin(self):
        """Test WebSocketStatsHandler when user is not admin"""
        handler = WebSocketStatsHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value="regular_user")
        handler.get_current_admin = MagicMock(return_value=None)
        
        with patch.object(handler, 'set_status') as mock_status:
            with patch.object(handler, 'write') as mock_write:
                handler.get()
                mock_status.assert_called_with(403)
                mock_write.assert_called_with("Forbidden")


class TestShareHandlers:
    """Test Share handlers for better coverage"""
    
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
    
    def test_shared_list_handler_nonexistent_share(self):
        """Test SharedListHandler with nonexistent share"""
        handler = SharedListHandler(self.mock_app, self.mock_request)
        handler.request.path = "/shared/nonexistent"
        
        with patch('aird.main.SHARES', {}):
            with patch.object(handler, 'set_status') as mock_status:
                with patch.object(handler, 'write') as mock_write:
                    handler.get('nonexistent')
                    mock_status.assert_called_with(404)
                    mock_write.assert_called_with("Invalid share link")
    
    def test_share_list_api_handler(self):
        """Test ShareListAPIHandler"""
        handler = ShareListAPIHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value="test_user")
        handler.write = MagicMock()
        
        with patch('aird.main.is_feature_enabled', return_value=True):
            with patch('aird.main.SHARES', {'share1': {'paths': ['file1.txt']}}):
                handler.get()
                handler.write.assert_called_once()
                # Check that it writes a dictionary with 'shares' key
                call_args = handler.write.call_args[0][0]
                assert 'shares' in call_args
    
    def test_share_list_api_handler_disabled(self):
        """Test ShareListAPIHandler when sharing is disabled"""
        handler = ShareListAPIHandler(self.mock_app, self.mock_request)
        handler.get_current_user = MagicMock(return_value="test_user")
        handler.write = MagicMock()
        
        with patch('aird.main.is_feature_enabled', return_value=False):
            handler.get()
            handler.write.assert_called_with({"error": "File sharing is disabled"})


class TestRustIntegration:
    """Test Rust integration and fallback behavior"""
    
    def test_rust_availability_variables(self):
        """Test that Rust availability variables are accessible"""
        # Test that RUST_AVAILABLE is a boolean
        assert isinstance(RUST_AVAILABLE, bool)
        
        # Test that HybridFileHandler and HybridCompressionHandler are accessible
        # (they might be None if Rust is not available)
        assert HybridFileHandler is None or hasattr(HybridFileHandler, '__call__')
        assert HybridCompressionHandler is None or hasattr(HybridCompressionHandler, '__call__')
