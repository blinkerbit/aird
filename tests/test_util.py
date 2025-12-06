"""Tests for aird/utils/util.py"""

import pytest
import os
import tempfile
import json
import sqlite3
import time
from unittest.mock import patch, MagicMock, PropertyMock

from aird.utils.util import (
    _load_shares,
    get_file_icon,
    join_path,
    is_within_root,
    is_valid_websocket_origin,
    WebSocketConnectionManager,
    FilterExpression,
    get_files_in_directory,
    is_video_file,
    is_audio_file,
    get_all_files_recursive,
    matches_glob_patterns,
    filter_files_by_patterns,
    cloud_root_dir,
    ensure_share_cloud_dir,
    sanitize_cloud_filename,
    is_cloud_relative_path,
    remove_cloud_file_if_exists,
    cleanup_share_cloud_dir_if_empty,
    remove_share_cloud_dir,
    parse_expression,
    evaluate_expression,
    get_current_feature_flags,
    get_current_websocket_config,
    is_feature_enabled,
    MMapFileHandler,
    MMAP_MIN_SIZE,
    CHUNK_SIZE,
)


class TestGetFileIcon:
    """Tests for get_file_icon function"""
    
    def test_python_file_icon(self):
        """Test Python file icon"""
        assert "🐍" in get_file_icon("test.py")
    
    def test_javascript_file_icon(self):
        """Test JavaScript file icon"""
        assert "🟨" in get_file_icon("test.js")
    
    def test_image_file_icon(self):
        """Test image file icon"""
        assert "🖼️" in get_file_icon("test.jpg")
        assert "🖼️" in get_file_icon("test.png")
    
    def test_video_file_icon(self):
        """Test video file icon"""
        assert "🎬" in get_file_icon("test.mp4")
    
    def test_audio_file_icon(self):
        """Test audio file icon"""
        assert "🎵" in get_file_icon("test.mp3")
    
    def test_readme_file_icon(self):
        """Test README file icon"""
        assert "📖" in get_file_icon("README.md")
        assert "📖" in get_file_icon("readme.txt")
    
    def test_license_file_icon(self):
        """Test license file icon"""
        assert "📜" in get_file_icon("LICENSE")
    
    def test_dockerfile_icon(self):
        """Test Dockerfile icon"""
        assert "🐳" in get_file_icon("Dockerfile")
    
    def test_default_icon(self):
        """Test default icon for unknown extension"""
        assert get_file_icon("unknown.xyz") == "📦"


class TestJoinPath:
    """Tests for join_path function"""
    
    def test_join_simple_paths(self):
        """Test joining simple path parts"""
        result = join_path("a", "b", "c")
        assert result == "a/b/c"
    
    def test_join_with_backslashes(self):
        """Test that backslashes are converted to forward slashes"""
        result = join_path("a\\b", "c\\d")
        assert "\\" not in result
    
    def test_join_single_part(self):
        """Test with single path part"""
        result = join_path("single")
        assert result == "single"


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


class TestIsValidWebsocketOrigin:
    """Tests for is_valid_websocket_origin function"""
    
    def create_mock_handler(self, host="localhost:8000", protocol="http"):
        """Helper to create a mock handler"""
        handler = MagicMock()
        handler.request.host = host
        handler.request.protocol = protocol
        return handler
    
    def test_empty_origin_returns_false(self):
        """Test that empty origin returns False"""
        handler = self.create_mock_handler()
        assert is_valid_websocket_origin(handler, "") is False
    
    def test_matching_origin_returns_true(self):
        """Test that matching origin returns True"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http")
        assert is_valid_websocket_origin(handler, "http://localhost:8000") is True
    
    def test_ws_scheme_with_http_protocol(self):
        """Test that ws:// is accepted with http:// protocol"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http")
        assert is_valid_websocket_origin(handler, "ws://localhost:8000") is True
    
    def test_mismatched_host_returns_false(self):
        """Test that mismatched host returns False"""
        handler = self.create_mock_handler(host="localhost:8000", protocol="http")
        assert is_valid_websocket_origin(handler, "http://example.com:8000") is False


class TestGetFilesInDirectory:
    """Tests for get_files_in_directory function"""
    
    def test_get_files_in_directory(self):
        """Test getting files in directory"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test files
            with open(os.path.join(temp_dir, "file1.txt"), 'w') as f:
                f.write("test1")
            with open(os.path.join(temp_dir, "file2.txt"), 'w') as f:
                f.write("test2")
            os.makedirs(os.path.join(temp_dir, "subdir"))
            
            files = get_files_in_directory(temp_dir)
            
            assert len(files) >= 2
            filenames = [f['name'] for f in files]
            assert "file1.txt" in filenames
            assert "file2.txt" in filenames


class TestIsVideoFile:
    """Tests for is_video_file function"""
    
    def test_video_extensions(self):
        """Test video file extensions"""
        assert is_video_file("test.mp4") is True
        assert is_video_file("test.avi") is True
        assert is_video_file("test.mkv") is True
        assert is_video_file("test.webm") is True
    
    def test_non_video_extensions(self):
        """Test non-video file extensions"""
        assert is_video_file("test.txt") is False
        assert is_video_file("test.jpg") is False


class TestIsAudioFile:
    """Tests for is_audio_file function"""
    
    def test_audio_extensions(self):
        """Test audio file extensions"""
        assert is_audio_file("test.mp3") is True
        assert is_audio_file("test.wav") is True
        assert is_audio_file("test.flac") is True
        assert is_audio_file("test.ogg") is True
    
    def test_non_audio_extensions(self):
        """Test non-audio file extensions"""
        assert is_audio_file("test.txt") is False
        assert is_audio_file("test.jpg") is False


class TestGetAllFilesRecursive:
    """Tests for get_all_files_recursive function"""
    
    def test_get_all_files_recursive(self):
        """Test getting all files recursively"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create nested structure
            os.makedirs(os.path.join(temp_dir, "subdir"))
            with open(os.path.join(temp_dir, "file1.txt"), 'w') as f:
                f.write("test1")
            with open(os.path.join(temp_dir, "subdir", "file2.txt"), 'w') as f:
                f.write("test2")
            
            files = get_all_files_recursive(temp_dir, temp_dir)
            
            assert len(files) == 2
            paths = [f for f in files]
            assert any("file1.txt" in p for p in paths)
            assert any("file2.txt" in p for p in paths)


class TestMatchesGlobPatterns:
    """Tests for matches_glob_patterns function"""
    
    def test_matches_simple_pattern(self):
        """Test matching simple glob pattern"""
        assert matches_glob_patterns("test.txt", ["*.txt"]) is True
        assert matches_glob_patterns("test.jpg", ["*.txt"]) is False
    
    def test_matches_multiple_patterns(self):
        """Test matching multiple patterns"""
        assert matches_glob_patterns("test.txt", ["*.txt", "*.jpg"]) is True
        assert matches_glob_patterns("test.jpg", ["*.txt", "*.jpg"]) is True
        assert matches_glob_patterns("test.png", ["*.txt", "*.jpg"]) is False
    
    def test_matches_empty_patterns(self):
        """Test with empty patterns list"""
        assert matches_glob_patterns("test.txt", []) is False


class TestFilterFilesByPatterns:
    """Tests for filter_files_by_patterns function"""
    
    def test_filter_files_by_allow_list(self):
        """Test filtering files by allow_list"""
        files = ["test.txt", "test.jpg", "test.png"]
        result = filter_files_by_patterns(files, allow_list=["*.txt", "*.jpg"])
        
        assert "test.txt" in result
        assert "test.jpg" in result
        assert "test.png" not in result
    
    def test_filter_files_by_avoid_list(self):
        """Test filtering files by avoid_list"""
        files = ["test.txt", "test.log", "test.jpg"]
        result = filter_files_by_patterns(files, avoid_list=["*.log"])
        
        assert "test.txt" in result
        assert "test.jpg" in result
        assert "test.log" not in result
    
    def test_filter_files_both_lists(self):
        """Test filtering with both allow_list and avoid_list"""
        files = ["test.txt", "test.log", "test.jpg", "test.png"]
        result = filter_files_by_patterns(
            files,
            allow_list=["*.txt", "*.jpg", "*.png"],
            avoid_list=["*.log"]
        )
        
        assert "test.txt" in result
        assert "test.jpg" in result
        assert "test.png" in result
        assert "test.log" not in result


class TestCloudRootDir:
    """Tests for cloud_root_dir function"""
    
    def test_cloud_root_dir_exists(self):
        """Test that cloud_root_dir returns a path"""
        # Import constants module to patch it
        import aird.constants
        original_root = aird.constants.ROOT_DIR
        original_folder = aird.constants.CLOUD_SHARE_FOLDER
        
        try:
            aird.constants.ROOT_DIR = '/test/root'
            aird.constants.CLOUD_SHARE_FOLDER = 'cloud_shares'
            result = cloud_root_dir()
            assert isinstance(result, str)
            assert len(result) > 0
            assert 'cloud_shares' in result
        finally:
            aird.constants.ROOT_DIR = original_root
            aird.constants.CLOUD_SHARE_FOLDER = original_folder


class TestEnsureShareCloudDir:
    """Tests for ensure_share_cloud_dir function"""
    
    def test_ensure_share_cloud_dir_creates_directory(self):
        """Test that ensure_share_cloud_dir creates directory"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch('aird.utils.util.cloud_root_dir', return_value=temp_dir):
                result = ensure_share_cloud_dir("test_share_123")
                
                assert os.path.exists(result)
                assert "test_share_123" in result


class TestSanitizeCloudFilename:
    """Tests for sanitize_cloud_filename function"""
    
    def test_sanitize_normal_filename(self):
        """Test sanitizing normal filename"""
        result = sanitize_cloud_filename("test_file.txt")
        assert result == "test_file.txt"
    
    def test_sanitize_filename_with_special_chars(self):
        """Test sanitizing filename with special characters"""
        result = sanitize_cloud_filename("test/file\\name.txt")
        # Should remove or replace special characters
        assert "/" not in result and "\\" not in result
    
    def test_sanitize_none_filename(self):
        """Test sanitizing None filename returns default"""
        result = sanitize_cloud_filename(None)
        # When None, function returns "cloud_file" as default
        assert result == "cloud_file"
    
    def test_sanitize_empty_filename(self):
        """Test sanitizing empty filename returns default"""
        result = sanitize_cloud_filename("")
        assert result == "cloud_file"
    
    def test_sanitize_filename_length_limit(self):
        """Test that filename is truncated to 128 characters"""
        long_name = "a" * 200
        result = sanitize_cloud_filename(long_name)
        assert len(result) <= 128


class TestIsCloudRelativePath:
    """Tests for is_cloud_relative_path function"""
    
    def test_is_cloud_relative_path_valid(self):
        """Test valid cloud relative path"""
        # Import constants module to patch it
        import aird.constants
        original_folder = aird.constants.CLOUD_SHARE_FOLDER
        
        try:
            aird.constants.CLOUD_SHARE_FOLDER = 'cloud_shares'
            # The function checks if path starts with CLOUD_SHARE_FOLDER/share_id/
            result = is_cloud_relative_path("share123", "cloud_shares/share123/file.txt")
            assert result is True
        finally:
            aird.constants.CLOUD_SHARE_FOLDER = original_folder
    
    def test_is_cloud_relative_path_invalid(self):
        """Test invalid cloud relative path"""
        import aird.constants
        original_folder = aird.constants.CLOUD_SHARE_FOLDER
        
        try:
            aird.constants.CLOUD_SHARE_FOLDER = 'cloud_shares'
            result = is_cloud_relative_path("share123", "file.txt")
            assert result is False
        finally:
            aird.constants.CLOUD_SHARE_FOLDER = original_folder
    
    def test_is_cloud_relative_path_wrong_share_id(self):
        """Test path with wrong share_id"""
        import aird.constants
        original_folder = aird.constants.CLOUD_SHARE_FOLDER
        
        try:
            aird.constants.CLOUD_SHARE_FOLDER = 'cloud_shares'
            result = is_cloud_relative_path("share123", "cloud_shares/share456/file.txt")
            assert result is False
        finally:
            aird.constants.CLOUD_SHARE_FOLDER = original_folder
    
    def test_is_cloud_relative_path_normalizes_backslashes(self):
        """Test that backslashes are normalized to forward slashes"""
        import aird.constants
        original_folder = aird.constants.CLOUD_SHARE_FOLDER
        
        try:
            aird.constants.CLOUD_SHARE_FOLDER = 'cloud_shares'
            result = is_cloud_relative_path("share123", "cloud_shares\\share123\\file.txt")
            assert result is True
        finally:
            aird.constants.CLOUD_SHARE_FOLDER = original_folder


class TestParseExpression:
    """Tests for parse_expression function"""
    
    def test_parse_simple_expression(self):
        """Test parsing simple expression"""
        result = parse_expression("hello")
        assert result is not None
        assert result == {'raw': 'hello'}
    
    def test_parse_complex_expression(self):
        """Test parsing complex expression"""
        result = parse_expression("hello AND world")
        assert result is not None
        assert result == {'raw': 'hello AND world'}
    
    def test_parse_empty_expression(self):
        """Test parsing empty expression"""
        result = parse_expression("")
        assert result == {'raw': ''}


class TestEvaluateExpression:
    """Tests for evaluate_expression function"""
    
    def test_evaluate_term_expression(self):
        """Test evaluating term expression"""
        parsed = {'raw': 'hello'}
        assert evaluate_expression("hello world", parsed) is True
        assert evaluate_expression("goodbye", parsed) is False
    
    def test_evaluate_empty_expression(self):
        """Test evaluating empty expression matches all"""
        parsed = {'raw': ''}
        assert evaluate_expression("anything", parsed) is True
    
    def test_evaluate_case_insensitive(self):
        """Test that evaluation is case insensitive"""
        parsed = {'raw': 'HELLO'}
        assert evaluate_expression("hello world", parsed) is True
    
    def test_evaluate_no_raw_key(self):
        """Test evaluating expression without raw key"""
        parsed = {}
        # Should return True when raw is empty/missing
        assert evaluate_expression("anything", parsed) is True


class TestGetCurrentFeatureFlags:
    """Tests for get_current_feature_flags function"""
    
    def test_get_current_feature_flags_from_constants(self):
        """Test getting feature flags from constants when DB is None"""
        # Import constants module to patch it
        import aird.constants
        original_flags = aird.constants.FEATURE_FLAGS.copy()
        original_conn = aird.constants.DB_CONN
        
        try:
            aird.constants.FEATURE_FLAGS = {'flag1': True}
            aird.constants.DB_CONN = None
            result = get_current_feature_flags()
            assert result == {'flag1': True}
        finally:
            aird.constants.FEATURE_FLAGS = original_flags
            aird.constants.DB_CONN = original_conn
    
    def test_get_current_feature_flags_from_db(self):
        """Test getting feature flags from database"""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE feature_flags (key TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("INSERT INTO feature_flags (key, value) VALUES ('db_flag', 1)")
        conn.commit()
        
        import aird.constants
        import aird.db
        original_flags = aird.constants.FEATURE_FLAGS.copy()
        original_conn = aird.constants.DB_CONN
        
        try:
            aird.constants.FEATURE_FLAGS = {'mem_flag': True}
            aird.constants.DB_CONN = conn
            # Mock the _load_feature_flags function - it's imported inside get_current_feature_flags
            with patch('aird.db._load_feature_flags', return_value={'db_flag': True}):
                result = get_current_feature_flags()
                # Should merge DB and memory flags - in-memory takes precedence
                assert 'mem_flag' in result
                assert result['mem_flag'] is True
                assert 'db_flag' in result
        finally:
            aird.constants.FEATURE_FLAGS = original_flags
            aird.constants.DB_CONN = original_conn
            conn.close()


class TestGetCurrentWebsocketConfig:
    """Tests for get_current_websocket_config function"""
    
    def test_get_current_websocket_config_from_constants(self):
        """Test getting websocket config from constants when DB is None"""
        # Import modules to patch them
        import aird.constants
        import aird.db
        original_config = aird.constants.WEBSOCKET_CONFIG.copy()
        original_conn = aird.db.DB_CONN
        
        try:
            aird.constants.WEBSOCKET_CONFIG = {'max_connections': 50}
            aird.db.DB_CONN = None
            result = get_current_websocket_config()
            assert result == {'max_connections': 50}
        finally:
            aird.constants.WEBSOCKET_CONFIG = original_config
            aird.db.DB_CONN = original_conn
    
    def test_get_current_websocket_config_from_db(self):
        """Test getting websocket config from database"""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE websocket_config (key TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("INSERT INTO websocket_config (key, value) VALUES ('max_connections', 100)")
        conn.commit()
        
        import aird.constants
        import aird.db
        original_config = aird.constants.WEBSOCKET_CONFIG.copy()
        original_conn = aird.db.DB_CONN
        
        try:
            aird.constants.WEBSOCKET_CONFIG = {'timeout': 30}
            aird.db.DB_CONN = conn
            # Mock the _load_websocket_config function
            with patch('aird.db._load_websocket_config', return_value={'max_connections': 100}):
                result = get_current_websocket_config()
                # DB values override constants
                assert result['max_connections'] == 100
                assert result['timeout'] == 30  # From constants
        finally:
            aird.constants.WEBSOCKET_CONFIG = original_config
            aird.db.DB_CONN = original_conn
            conn.close()


class TestIsFeatureEnabled:
    """Tests for is_feature_enabled function"""
    
    def test_is_feature_enabled_default_false(self):
        """Test default value is False"""
        with patch('aird.utils.util.get_current_feature_flags', return_value={}):
            result = is_feature_enabled('nonexistent_feature')
            assert result is False
    
    def test_is_feature_enabled_from_flags(self):
        """Test reading from feature flags"""
        with patch('aird.utils.util.get_current_feature_flags', return_value={'my_feature': True}):
            result = is_feature_enabled('my_feature')
            assert result is True
    
    def test_is_feature_enabled_custom_default(self):
        """Test custom default value"""
        with patch('aird.utils.util.get_current_feature_flags', return_value={}):
            result = is_feature_enabled('nonexistent_feature', default=True)
            assert result is True


class TestLoadShares:
    """Tests for _load_shares function"""
    
    def test_load_shares_empty_database(self):
        """Test loading shares from empty database"""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE shares (
                id TEXT PRIMARY KEY,
                created TEXT,
                paths TEXT,
                allowed_users TEXT,
                secret_token TEXT,
                share_type TEXT,
                allow_list TEXT,
                avoid_list TEXT,
                expiry_date TEXT
            )
        """)
        conn.commit()
        
        result = _load_shares(conn)
        assert result == {}
        conn.close()
    
    def test_load_shares_with_full_schema(self):
        """Test loading shares with all columns"""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE shares (
                id TEXT PRIMARY KEY,
                created TEXT,
                paths TEXT,
                allowed_users TEXT,
                secret_token TEXT,
                share_type TEXT,
                allow_list TEXT,
                avoid_list TEXT,
                expiry_date TEXT
            )
        """)
        conn.execute("""
            INSERT INTO shares VALUES (
                'share1', '2024-01-01', '["path1.txt", "path2.txt"]',
                '["user1", "user2"]', 'token123', 'static',
                '["*.txt"]', '["*.log"]', '2024-12-31'
            )
        """)
        conn.commit()
        
        result = _load_shares(conn)
        
        assert 'share1' in result
        assert result['share1']['paths'] == ['path1.txt', 'path2.txt']
        assert result['share1']['allowed_users'] == ['user1', 'user2']
        assert result['share1']['secret_token'] == 'token123'
        assert result['share1']['share_type'] == 'static'
        assert result['share1']['allow_list'] == ['*.txt']
        assert result['share1']['avoid_list'] == ['*.log']
        assert result['share1']['expiry_date'] == '2024-12-31'
        conn.close()
    
    def test_load_shares_minimal_schema(self):
        """Test loading shares with minimal schema (old database)"""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE shares (
                id TEXT PRIMARY KEY,
                created TEXT,
                paths TEXT
            )
        """)
        conn.execute("""
            INSERT INTO shares VALUES ('share1', '2024-01-01', '["path1.txt"]')
        """)
        conn.commit()
        
        result = _load_shares(conn)
        
        assert 'share1' in result
        assert result['share1']['paths'] == ['path1.txt']
        assert result['share1']['allowed_users'] is None
        assert result['share1']['secret_token'] is None
        conn.close()
    
    def test_load_shares_invalid_json(self):
        """Test loading shares with invalid JSON in paths"""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE shares (
                id TEXT PRIMARY KEY,
                created TEXT,
                paths TEXT
            )
        """)
        conn.execute("""
            INSERT INTO shares VALUES ('share1', '2024-01-01', 'invalid json')
        """)
        conn.commit()
        
        result = _load_shares(conn)
        
        assert 'share1' in result
        assert result['share1']['paths'] == []
        conn.close()


class TestFilterExpression:
    """Tests for FilterExpression class"""
    
    def test_empty_expression_matches_all(self):
        """Test that empty expression matches everything"""
        fe = FilterExpression("")
        assert fe.matches("anything") is True
        assert fe.matches("hello world") is True
    
    def test_simple_term_matching(self):
        """Test simple term matching"""
        fe = FilterExpression("hello")
        assert fe.matches("hello world") is True
        assert fe.matches("HELLO WORLD") is True  # Case insensitive
        assert fe.matches("goodbye") is False
    
    def test_quoted_expression(self):
        """Test quoted expression (literal matching)"""
        fe = FilterExpression('"hello AND world"')
        assert fe.matches("hello AND world") is True
        assert fe.matches("hello") is False
    
    def test_and_expression(self):
        """Test AND expression"""
        fe = FilterExpression("hello AND world")
        assert fe.matches("hello world") is True
        assert fe.matches("world hello there") is True
        assert fe.matches("hello") is False
        assert fe.matches("world") is False
    
    def test_or_expression(self):
        """Test OR expression"""
        fe = FilterExpression("hello OR world")
        assert fe.matches("hello") is True
        assert fe.matches("world") is True
        assert fe.matches("goodbye") is False
    
    def test_complex_expression_with_parentheses(self):
        """Test complex expression with parentheses"""
        fe = FilterExpression("(hello OR hi) AND world")
        assert fe.matches("hello world") is True
        assert fe.matches("hi world") is True
        assert fe.matches("hello") is False
    
    def test_escaped_expression(self):
        """Test escaped expression (forced literal)"""
        fe = FilterExpression("\\hello AND world")
        # The backslash forces literal interpretation
        assert fe.matches("hello AND world") is True
    
    def test_str_representation(self):
        """Test string representation"""
        fe = FilterExpression("test expression")
        assert "test expression" in str(fe)


class TestWebSocketConnectionManager:
    """Tests for WebSocketConnectionManager class"""
    
    @patch('aird.utils.util.tornado.ioloop.IOLoop')
    def test_add_connection(self, mock_ioloop):
        """Test adding a connection"""
        mock_ioloop.current.return_value = MagicMock()
        manager = WebSocketConnectionManager("test", default_max_connections=10)
        
        conn = MagicMock()
        result = manager.add_connection(conn)
        
        assert result is True
        assert conn in manager.connections
    
    @patch('aird.utils.util.tornado.ioloop.IOLoop')
    def test_add_connection_over_limit(self, mock_ioloop):
        """Test adding connection when over limit"""
        mock_ioloop.current.return_value = MagicMock()
        manager = WebSocketConnectionManager("test", default_max_connections=2)
        
        conn1 = MagicMock()
        conn2 = MagicMock()
        conn3 = MagicMock()
        
        manager.add_connection(conn1)
        manager.add_connection(conn2)
        result = manager.add_connection(conn3)
        
        assert result is False
        assert conn3 not in manager.connections
    
    @patch('aird.utils.util.tornado.ioloop.IOLoop')
    def test_remove_connection(self, mock_ioloop):
        """Test removing a connection"""
        mock_ioloop.current.return_value = MagicMock()
        manager = WebSocketConnectionManager("test")
        
        conn = MagicMock()
        manager.add_connection(conn)
        manager.remove_connection(conn)
        
        assert conn not in manager.connections
    
    @patch('aird.utils.util.tornado.ioloop.IOLoop')
    def test_update_activity(self, mock_ioloop):
        """Test updating connection activity"""
        mock_ioloop.current.return_value = MagicMock()
        manager = WebSocketConnectionManager("test")
        
        conn = MagicMock()
        manager.add_connection(conn)
        
        old_activity = manager.last_activity.get(conn)
        time.sleep(0.01)
        manager.update_activity(conn)
        new_activity = manager.last_activity.get(conn)
        
        assert new_activity >= old_activity
    
    @patch('aird.utils.util.tornado.ioloop.IOLoop')
    def test_get_stats(self, mock_ioloop):
        """Test getting connection statistics"""
        mock_ioloop.current.return_value = MagicMock()
        manager = WebSocketConnectionManager("test", default_max_connections=100, default_idle_timeout=300)
        
        conn = MagicMock()
        manager.add_connection(conn)
        
        stats = manager.get_stats()
        
        assert stats['active_connections'] == 1
        assert stats['max_connections'] == 100
        assert stats['idle_timeout'] == 300
    
    @patch('aird.utils.util.tornado.ioloop.IOLoop')
    def test_broadcast_message(self, mock_ioloop):
        """Test broadcasting message to all connections"""
        mock_ioloop.current.return_value = MagicMock()
        manager = WebSocketConnectionManager("test")
        
        conn1 = MagicMock()
        conn2 = MagicMock()
        manager.add_connection(conn1)
        manager.add_connection(conn2)
        
        manager.broadcast_message("test message")
        
        conn1.write_message.assert_called_with("test message")
        conn2.write_message.assert_called_with("test message")
    
    @patch('aird.utils.util.tornado.ioloop.IOLoop')
    def test_broadcast_message_with_filter(self, mock_ioloop):
        """Test broadcasting message with filter function"""
        mock_ioloop.current.return_value = MagicMock()
        manager = WebSocketConnectionManager("test")
        
        conn1 = MagicMock()
        conn1.should_receive = True
        conn2 = MagicMock()
        conn2.should_receive = False
        
        manager.add_connection(conn1)
        manager.add_connection(conn2)
        
        manager.broadcast_message("test message", filter_func=lambda c: getattr(c, 'should_receive', False))
        
        conn1.write_message.assert_called_with("test message")
        conn2.write_message.assert_not_called()


class TestRemoveCloudFileIfExists:
    """Tests for remove_cloud_file_if_exists function"""
    
    def test_remove_existing_cloud_file(self):
        """Test removing an existing cloud file"""
        with tempfile.TemporaryDirectory() as temp_dir:
            import aird.constants
            original_root = aird.constants.ROOT_DIR
            original_folder = aird.constants.CLOUD_SHARE_FOLDER
            
            try:
                aird.constants.ROOT_DIR = temp_dir
                aird.constants.CLOUD_SHARE_FOLDER = 'cloud_shares'
                
                # Create the cloud file
                share_dir = os.path.join(temp_dir, 'cloud_shares', 'share123')
                os.makedirs(share_dir, exist_ok=True)
                file_path = os.path.join(share_dir, 'test.txt')
                with open(file_path, 'w') as f:
                    f.write("test")
                
                relative_path = 'cloud_shares/share123/test.txt'
                remove_cloud_file_if_exists('share123', relative_path)
                
                assert not os.path.exists(file_path)
            finally:
                aird.constants.ROOT_DIR = original_root
                aird.constants.CLOUD_SHARE_FOLDER = original_folder
    
    def test_remove_nonexistent_file_no_error(self):
        """Test that removing non-existent file doesn't raise error"""
        with tempfile.TemporaryDirectory() as temp_dir:
            import aird.constants
            original_root = aird.constants.ROOT_DIR
            original_folder = aird.constants.CLOUD_SHARE_FOLDER
            
            try:
                aird.constants.ROOT_DIR = temp_dir
                aird.constants.CLOUD_SHARE_FOLDER = 'cloud_shares'
                
                # This should not raise an error
                remove_cloud_file_if_exists('share123', 'cloud_shares/share123/nonexistent.txt')
            finally:
                aird.constants.ROOT_DIR = original_root
                aird.constants.CLOUD_SHARE_FOLDER = original_folder


class TestCleanupShareCloudDirIfEmpty:
    """Tests for cleanup_share_cloud_dir_if_empty function"""
    
    def test_cleanup_empty_directory(self):
        """Test that empty cloud directory is removed"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch('aird.utils.util.cloud_root_dir', return_value=temp_dir):
                share_dir = os.path.join(temp_dir, 'share123')
                os.makedirs(share_dir)
                
                cleanup_share_cloud_dir_if_empty('share123')
                
                assert not os.path.exists(share_dir)
    
    def test_keep_nonempty_directory(self):
        """Test that non-empty directory is not removed"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch('aird.utils.util.cloud_root_dir', return_value=temp_dir):
                share_dir = os.path.join(temp_dir, 'share123')
                os.makedirs(share_dir)
                
                # Create a file in the directory
                with open(os.path.join(share_dir, 'file.txt'), 'w') as f:
                    f.write("test")
                
                cleanup_share_cloud_dir_if_empty('share123')
                
                assert os.path.exists(share_dir)


class TestRemoveShareCloudDir:
    """Tests for remove_share_cloud_dir function"""
    
    def test_remove_share_cloud_dir(self):
        """Test removing entire cloud share directory"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch('aird.utils.util.cloud_root_dir', return_value=temp_dir):
                share_dir = os.path.join(temp_dir, 'share123')
                os.makedirs(share_dir)
                
                # Create some files
                with open(os.path.join(share_dir, 'file.txt'), 'w') as f:
                    f.write("test")
                
                remove_share_cloud_dir('share123')
                
                assert not os.path.exists(share_dir)
    
    def test_remove_empty_share_id_no_error(self):
        """Test that empty share_id doesn't cause error"""
        # This should not raise an error
        remove_share_cloud_dir('')
        remove_share_cloud_dir(None)


class TestMMapFileHandler:
    """Tests for MMapFileHandler class"""
    
    def test_should_use_mmap_small_file(self):
        """Test that small files don't use mmap"""
        assert MMapFileHandler.should_use_mmap(100) is False
        assert MMapFileHandler.should_use_mmap(MMAP_MIN_SIZE - 1) is False
    
    def test_should_use_mmap_large_file(self):
        """Test that large files use mmap"""
        assert MMapFileHandler.should_use_mmap(MMAP_MIN_SIZE) is True
        assert MMapFileHandler.should_use_mmap(MMAP_MIN_SIZE * 10) is True
    
    def test_find_line_offsets_small_file(self):
        """Test finding line offsets in small file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("line1\nline2\nline3\n")
            temp_path = f.name
        
        try:
            offsets = MMapFileHandler.find_line_offsets(temp_path)
            assert len(offsets) >= 3
            assert 0 in offsets  # First line starts at 0
        finally:
            os.unlink(temp_path)
    
    def test_find_line_offsets_with_max_lines(self):
        """Test finding line offsets with max_lines limit"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            for i in range(100):
                f.write(f"line{i}\n")
            temp_path = f.name
        
        try:
            offsets = MMapFileHandler.find_line_offsets(temp_path, max_lines=10)
            assert len(offsets) <= 10
        finally:
            os.unlink(temp_path)
    
    def test_search_in_file_small_file(self):
        """Test searching in small file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("hello world\ngoodbye world\nhello again\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "hello")
            assert len(results) == 2
            assert results[0]['line_number'] == 1
            assert results[1]['line_number'] == 3
        finally:
            os.unlink(temp_path)
    
    def test_search_in_file_no_matches(self):
        """Test searching with no matches"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("hello world\ngoodbye world\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "notfound")
            assert len(results) == 0
        finally:
            os.unlink(temp_path)
    
    def test_search_in_file_max_results(self):
        """Test searching with max_results limit"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            for i in range(100):
                f.write(f"test line {i}\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "test", max_results=5)
            assert len(results) == 5
        finally:
            os.unlink(temp_path)
    
    def test_search_in_file_match_positions(self):
        """Test that match positions are correctly reported"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("hello hello hello\n")
            temp_path = f.name
        
        try:
            results = MMapFileHandler.search_in_file(temp_path, "hello")
            assert len(results) == 1
            assert len(results[0]['match_positions']) == 3
            assert 0 in results[0]['match_positions']
        finally:
            os.unlink(temp_path)


class TestAdditionalFileIcons:
    """Additional tests for get_file_icon function"""
    
    def test_makefile_icon(self):
        """Test Makefile icon"""
        assert "🔨" in get_file_icon("Makefile")
        assert "🔨" in get_file_icon("CMakeLists.txt")
    
    def test_gitignore_icon(self):
        """Test .gitignore icon"""
        assert "🔧" in get_file_icon(".gitignore")
    
    def test_env_file_icon(self):
        """Test .env file icon"""
        assert "🔐" in get_file_icon(".env")
        assert "🔐" in get_file_icon(".env.local")
    
    def test_pdf_icon(self):
        """Test PDF file icon"""
        assert "📕" in get_file_icon("document.pdf")
    
    def test_archive_icon(self):
        """Test archive file icon"""
        assert "🗜️" in get_file_icon("archive.zip")
        assert "🗜️" in get_file_icon("archive.tar.gz")
    
    def test_database_icon(self):
        """Test database file icon"""
        assert "🗃️" in get_file_icon("data.sqlite3")
        assert "🗃️" in get_file_icon("data.db")
    
    def test_notebook_icon(self):
        """Test Jupyter notebook icon"""
        assert "📓" in get_file_icon("analysis.ipynb")
    
    def test_rust_icon(self):
        """Test Rust file icon"""
        assert "🦀" in get_file_icon("main.rs")
    
    def test_go_icon(self):
        """Test Go file icon"""
        assert "🐹" in get_file_icon("main.go")


class TestFilterExpressionEdgeCases:
    """Edge case tests for FilterExpression"""
    
    def test_nested_parentheses(self):
        """Test nested parentheses"""
        fe = FilterExpression("((hello OR hi) AND world) OR goodbye")
        assert fe.matches("hello world") is True
        assert fe.matches("goodbye") is True
    
    def test_multiple_and_operators(self):
        """Test multiple AND operators"""
        fe = FilterExpression("a AND b AND c")
        assert fe.matches("a b c") is True
        assert fe.matches("a b") is False
    
    def test_multiple_or_operators(self):
        """Test multiple OR operators"""
        fe = FilterExpression("a OR b OR c")
        assert fe.matches("a") is True
        assert fe.matches("b") is True
        assert fe.matches("c") is True
        assert fe.matches("d") is False
    
    def test_mixed_case_operators(self):
        """Test that operators are case insensitive"""
        fe = FilterExpression("hello and world")
        assert fe.matches("hello world") is True
        
        fe2 = FilterExpression("hello OR world")
        assert fe2.matches("hello") is True
    
    def test_whitespace_in_expression(self):
        """Test expressions with extra whitespace"""
        fe = FilterExpression("  hello   AND   world  ")
        assert fe.matches("hello world") is True
