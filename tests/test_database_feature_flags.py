"""Tests for aird/database/feature_flags.py"""

import pytest
import sqlite3
from unittest.mock import patch, MagicMock

from aird.database.feature_flags import (
    load_feature_flags,
    save_feature_flags,
    load_websocket_config,
    save_websocket_config,
    is_feature_enabled
)


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database for testing"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE feature_flags (key TEXT PRIMARY KEY, value INTEGER)")
    conn.execute("CREATE TABLE websocket_config (key TEXT PRIMARY KEY, value INTEGER)")
    conn.commit()
    yield conn
    conn.close()


class TestLoadFeatureFlags:
    """Tests for load_feature_flags function"""
    
    def test_load_empty_flags(self, db_conn):
        """Test loading when no flags exist"""
        result = load_feature_flags(db_conn)
        assert result == {}
    
    def test_load_existing_flags(self, db_conn):
        """Test loading existing feature flags"""
        db_conn.execute("INSERT INTO feature_flags (key, value) VALUES ('flag1', 1)")
        db_conn.execute("INSERT INTO feature_flags (key, value) VALUES ('flag2', 0)")
        db_conn.commit()
        
        result = load_feature_flags(db_conn)
        assert result == {'flag1': True, 'flag2': False}
    
    def test_load_flags_converts_to_bool(self, db_conn):
        """Test that values are converted to boolean"""
        db_conn.execute("INSERT INTO feature_flags (key, value) VALUES ('flag1', 5)")
        db_conn.commit()
        
        result = load_feature_flags(db_conn)
        assert result['flag1'] is True
    
    def test_load_flags_exception_returns_empty(self):
        """Test that exceptions return empty dict"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database error")
        
        result = load_feature_flags(mock_conn)
        assert result == {}


class TestSaveFeatureFlags:
    """Tests for save_feature_flags function"""
    
    def test_save_new_flags(self, db_conn):
        """Test saving new feature flags"""
        flags = {'flag1': True, 'flag2': False}
        save_feature_flags(db_conn, flags)
        
        result = load_feature_flags(db_conn)
        assert result == flags
    
    def test_save_updates_existing_flags(self, db_conn):
        """Test that saving updates existing flags"""
        db_conn.execute("INSERT INTO feature_flags (key, value) VALUES ('flag1', 0)")
        db_conn.commit()
        
        save_feature_flags(db_conn, {'flag1': True})
        
        result = load_feature_flags(db_conn)
        assert result['flag1'] is True
    
    def test_save_converts_bool_to_int(self, db_conn):
        """Test that boolean values are converted to integers"""
        save_feature_flags(db_conn, {'flag1': True, 'flag2': False})
        
        cursor = db_conn.execute("SELECT key, value FROM feature_flags ORDER BY key")
        rows = cursor.fetchall()
        assert rows == [('flag1', 1), ('flag2', 0)]
    
    def test_save_flags_exception_handled(self):
        """Test that exceptions during save are handled"""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=Exception("Database error"))
        
        # Should not raise exception
        save_feature_flags(mock_conn, {'flag1': True})


class TestLoadWebsocketConfig:
    """Tests for load_websocket_config function"""
    
    def test_load_empty_config(self, db_conn):
        """Test loading when no config exists"""
        result = load_websocket_config(db_conn)
        assert result == {}
    
    def test_load_existing_config(self, db_conn):
        """Test loading existing websocket config"""
        db_conn.execute("INSERT INTO websocket_config (key, value) VALUES ('max_connections', 100)")
        db_conn.execute("INSERT INTO websocket_config (key, value) VALUES ('timeout', 30)")
        db_conn.commit()
        
        result = load_websocket_config(db_conn)
        assert result == {'max_connections': 100, 'timeout': 30}
    
    def test_load_config_creates_table_if_missing(self):
        """Test that load creates table if it doesn't exist"""
        conn = sqlite3.connect(":memory:")
        result = load_websocket_config(conn)
        assert result == {}
        
        # Verify table was created
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='websocket_config'")
        assert cursor.fetchone() is not None
        conn.close()
    
    def test_load_config_exception_returns_empty(self):
        """Test that exceptions return empty dict"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database error")
        
        result = load_websocket_config(mock_conn)
        assert result == {}


class TestSaveWebsocketConfig:
    """Tests for save_websocket_config function"""
    
    def test_save_new_config(self, db_conn):
        """Test saving new websocket config"""
        config = {'max_connections': 100, 'timeout': 30}
        save_websocket_config(db_conn, config)
        
        result = load_websocket_config(db_conn)
        assert result == config
    
    def test_save_updates_existing_config(self, db_conn):
        """Test that saving updates existing config"""
        db_conn.execute("INSERT INTO websocket_config (key, value) VALUES ('timeout', 10)")
        db_conn.commit()
        
        save_websocket_config(db_conn, {'timeout': 60})
        
        result = load_websocket_config(db_conn)
        assert result['timeout'] == 60
    
    def test_save_config_creates_table_if_missing(self):
        """Test that save creates table if it doesn't exist"""
        conn = sqlite3.connect(":memory:")
        save_websocket_config(conn, {'key1': 100})
        
        # Verify table was created and data was saved
        cursor = conn.execute("SELECT value FROM websocket_config WHERE key = 'key1'")
        assert cursor.fetchone()[0] == 100
        conn.close()
    
    def test_save_config_exception_handled(self):
        """Test that exceptions during save are handled"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database error")
        
        # Should not raise exception
        save_websocket_config(mock_conn, {'key1': 100})


class TestIsFeatureEnabled:
    """Tests for is_feature_enabled function"""
    
    def test_feature_enabled_default_false(self):
        """Test default value is False"""
        with patch('aird.database.db.get_db_conn', return_value=None), \
             patch('aird.constants.FEATURE_FLAGS', {}):
            result = is_feature_enabled('nonexistent_feature')
            assert result is False
    
    def test_feature_enabled_custom_default(self):
        """Test custom default value"""
        with patch('aird.database.db.get_db_conn', return_value=None), \
             patch('aird.constants.FEATURE_FLAGS', {}):
            result = is_feature_enabled('nonexistent_feature', default=True)
            assert result is True
    
    def test_feature_enabled_from_constants(self):
        """Test reading from FEATURE_FLAGS constant"""
        with patch('aird.database.db.get_db_conn', return_value=None), \
             patch('aird.constants.FEATURE_FLAGS', {'my_feature': True}):
            result = is_feature_enabled('my_feature')
            assert result is True
    
    def test_feature_enabled_from_database(self):
        """Test reading from database"""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE feature_flags (key TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("INSERT INTO feature_flags (key, value) VALUES ('db_feature', 1)")
        conn.commit()
        
        with patch('aird.database.db.get_db_conn', return_value=conn), \
             patch('aird.constants.FEATURE_FLAGS', {}):
            result = is_feature_enabled('db_feature')
            assert result is True
        
        conn.close()
    
    def test_feature_database_overrides_constants(self):
        """Test that database values override constant values"""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE feature_flags (key TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("INSERT INTO feature_flags (key, value) VALUES ('my_feature', 0)")
        conn.commit()
        
        with patch('aird.database.db.get_db_conn', return_value=conn), \
             patch('aird.constants.FEATURE_FLAGS', {'my_feature': True}):
            result = is_feature_enabled('my_feature')
            assert result is False  # Database value (0/False) overrides constant (True)
        
        conn.close()
    
    def test_feature_enabled_exception_uses_default(self):
        """Test that exceptions fall back to default"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database error")
        
        with patch('aird.database.db.get_db_conn', return_value=mock_conn), \
             patch('aird.constants.FEATURE_FLAGS', {'my_feature': True}):
            result = is_feature_enabled('my_feature')
            # Should use the value from FEATURE_FLAGS since DB failed
            assert result is True
