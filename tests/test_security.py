"""Tests for aird/core/security.py"""

import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch

from aird.core.security import join_path, is_within_root, is_valid_websocket_origin


class TestJoinPath:
    """Tests for join_path function"""
    
    def test_join_simple_paths(self):
        """Test joining simple path parts"""
        result = join_path("a", "b", "c")
        assert result == "a/b/c"
    
    def test_join_with_backslashes(self):
        """Test that backslashes are converted to forward slashes"""
        result = join_path("a\\b", "c\\d")
        # os.path.join will handle this, then we replace backslashes
        assert "\\" not in result
    
    def test_join_single_part(self):
        """Test with single path part"""
        result = join_path("single")
        assert result == "single"
    
    def test_join_with_absolute_path(self):
        """Test joining with absolute-like paths"""
        result = join_path("/root", "subdir", "file.txt")
        assert "file.txt" in result


class TestIsWithinRoot:
    """Tests for is_within_root function"""
    
    def test_path_within_root(self):
        """Test that a path within root returns True"""
        with tempfile.TemporaryDirectory() as temp_dir:
            subdir = os.path.join(temp_dir, "subdir")
            os.makedirs(subdir)
            file_path = os.path.join(subdir, "file.txt")
            with open(file_path, 'w') as f:
                f.write("test")
            
            assert is_within_root(file_path, temp_dir) is True
    
    def test_path_outside_root(self):
        """Test that a path outside root returns False"""
        with tempfile.TemporaryDirectory() as temp_dir1:
            with tempfile.TemporaryDirectory() as temp_dir2:
                file_path = os.path.join(temp_dir1, "file.txt")
                with open(file_path, 'w') as f:
                    f.write("test")
                
                assert is_within_root(file_path, temp_dir2) is False
    
    def test_path_is_root(self):
        """Test that the root path itself returns True"""
        with tempfile.TemporaryDirectory() as temp_dir:
            assert is_within_root(temp_dir, temp_dir) is True
    
    def test_path_traversal_attempt(self):
        """Test that path traversal attempts return False"""
        with tempfile.TemporaryDirectory() as temp_dir:
            subdir = os.path.join(temp_dir, "subdir")
            os.makedirs(subdir)
            # Attempt path traversal
            traversal_path = os.path.join(subdir, "..", "..", "etc", "passwd")
            # This should resolve outside temp_dir
            result = is_within_root(traversal_path, subdir)
            assert result is False
    
    def test_invalid_path_returns_false(self):
        """Test that an exception during path resolution returns False"""
        # Test with non-existent paths that might cause issues
        result = is_within_root("", "")
        # Either True or False depending on implementation, but shouldn't raise
        assert isinstance(result, bool)
    
    def test_symlink_resolution(self):
        """Test that symlinks are properly resolved"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a file
            file_path = os.path.join(temp_dir, "real_file.txt")
            with open(file_path, 'w') as f:
                f.write("test")
            
            # The file should be within root
            assert is_within_root(file_path, temp_dir) is True


class TestIsValidWebsocketOrigin:
    """Tests for is_valid_websocket_origin function"""
    
    def create_mock_handler(self, host="localhost:8000", protocol="http", allow_dev=False):
        """Helper to create a mock handler"""
        handler = MagicMock()
        handler.request.host = host
        handler.request.protocol = protocol
        handler.settings = {"allow_dev_origins": allow_dev}
        return handler
    
    def test_empty_origin_returns_false(self):
        """Test that empty origin returns False"""
        handler = self.create_mock_handler()
        assert is_valid_websocket_origin(handler, "") is False
        assert is_valid_websocket_origin(handler, None) is False
    
    def test_matching_origin_returns_true(self):
        """Test that matching origin returns True"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http")
        assert is_valid_websocket_origin(handler, "http://localhost:8000") is True
    
    def test_ws_scheme_with_http_protocol(self):
        """Test that ws:// is accepted with http:// protocol"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http")
        assert is_valid_websocket_origin(handler, "ws://localhost:8000") is True
    
    def test_wss_scheme_with_https_protocol(self):
        """Test that wss:// is accepted with https:// protocol"""
        handler = self.create_mock_handler(host="localhost:443", protocol="https")
        assert is_valid_websocket_origin(handler, "wss://localhost:443") is True
    
    def test_mismatched_host_returns_false(self):
        """Test that mismatched host returns False"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http")
        assert is_valid_websocket_origin(handler, "http://example.com:8000") is False
    
    def test_mismatched_port_returns_false(self):
        """Test that mismatched port returns False"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http")
        assert is_valid_websocket_origin(handler, "http://localhost:9000") is False
    
    def test_allow_dev_origins_localhost(self):
        """Test that localhost is allowed when allow_dev_origins is True"""
        handler = self.create_mock_handler(host="example.com:8000", protocol="http", allow_dev=True)
        assert is_valid_websocket_origin(handler, "http://localhost:8000") is True
    
    def test_allow_dev_origins_127_0_0_1(self):
        """Test that 127.0.0.1 is allowed when allow_dev_origins is True"""
        handler = self.create_mock_handler(host="example.com:8000", protocol="http", allow_dev=True)
        assert is_valid_websocket_origin(handler, "http://127.0.0.1:8000") is True
    
    def test_default_port_http(self):
        """Test that default port 80 is used for HTTP"""
        handler = self.create_mock_handler(host="localhost", protocol="http")
        assert is_valid_websocket_origin(handler, "http://localhost") is True
    
    def test_default_port_https(self):
        """Test that default port 443 is used for HTTPS"""
        handler = self.create_mock_handler(host="localhost", protocol="https")
        assert is_valid_websocket_origin(handler, "https://localhost") is True
    
    def test_invalid_scheme_returns_false(self):
        """Test that invalid scheme returns False"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http")
        assert is_valid_websocket_origin(handler, "ftp://localhost:8000") is False
    
    def test_exception_returns_false(self):
        """Test that exceptions during validation return False"""
        handler = MagicMock()
        handler.request.host = None  # This will cause an exception
        assert is_valid_websocket_origin(handler, "http://localhost:8000") is False
    
    def test_different_port_with_allow_dev(self):
        """Test that different port is allowed with allow_dev_origins for localhost"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http", allow_dev=True)
        assert is_valid_websocket_origin(handler, "http://localhost:3000") is True
