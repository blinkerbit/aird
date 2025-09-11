"""
Unit tests for database functions in aird.main module.
"""

import sqlite3
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from aird.main import (
    _init_db,
    _load_feature_flags,
    _save_feature_flags,
    _load_shares,
    _insert_share,
    _delete_share,
    get_current_feature_flags,
    is_feature_enabled,
    FEATURE_FLAGS
)


class TestDatabaseInit:
    """Test database initialization"""
    
    def test_init_db_creates_tables(self):
        """Test that _init_db creates required tables"""
        # Use in-memory database for testing
        conn = sqlite3.connect(":memory:")
        
        _init_db(conn)
        
        # Check that feature_flags table exists
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='feature_flags'")
        assert cursor.fetchone() is not None
        
        # Check that shares table exists
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shares'")
        assert cursor.fetchone() is not None
        
        # Check feature_flags table structure
        cursor = conn.execute("PRAGMA table_info(feature_flags)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "key" in columns
        assert "value" in columns
        assert columns["key"] == "TEXT"
        assert columns["value"] == "INTEGER"
        
        # Check shares table structure
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "id" in columns
        assert "created" in columns
        assert "paths" in columns
        
        conn.close()


class TestFeatureFlags:
    """Test feature flags database operations"""
    
    def setup_method(self):
        """Set up test database for each test"""
        self.conn = sqlite3.connect(":memory:")
        _init_db(self.conn)
    
    def teardown_method(self):
        """Clean up test database after each test"""
        self.conn.close()
    
    def test_load_feature_flags_empty(self):
        """Test loading feature flags from empty database"""
        flags = _load_feature_flags(self.conn)
        assert flags == {}
    
    def test_save_and_load_feature_flags(self):
        """Test saving and loading feature flags"""
        test_flags = {
            "file_upload": True,
            "file_delete": False,
            "file_edit": True
        }
        
        _save_feature_flags(self.conn, test_flags)
        loaded_flags = _load_feature_flags(self.conn)
        
        assert loaded_flags == test_flags
    
    def test_save_feature_flags_update_existing(self):
        """Test updating existing feature flags"""
        # Insert initial flags
        initial_flags = {"file_upload": True, "file_delete": False}
        _save_feature_flags(self.conn, initial_flags)
        
        # Update flags
        updated_flags = {"file_upload": False, "file_delete": True, "file_edit": True}
        _save_feature_flags(self.conn, updated_flags)
        
        loaded_flags = _load_feature_flags(self.conn)
        assert loaded_flags == updated_flags
    
    def test_load_feature_flags_database_error(self):
        """Test loading feature flags when database error occurs"""
        # Close connection to simulate error
        self.conn.close()
        
        flags = _load_feature_flags(self.conn)
        assert flags == {}
    
    def test_save_feature_flags_database_error(self):
        """Test saving feature flags when database error occurs"""
        # Close connection to simulate error
        self.conn.close()
        
        test_flags = {"file_upload": True}
        # Should not raise exception
        _save_feature_flags(self.conn, test_flags)


class TestShares:
    """Test shares database operations"""
    
    def setup_method(self):
        """Set up test database for each test"""
        self.conn = sqlite3.connect(":memory:")
        _init_db(self.conn)
    
    def teardown_method(self):
        """Clean up test database after each test"""
        self.conn.close()
    
    def test_load_shares_empty(self):
        """Test loading shares from empty database"""
        shares = _load_shares(self.conn)
        assert shares == {}
    
    def test_insert_and_load_share(self):
        """Test inserting and loading a share"""
        share_id = "test_share_123"
        created = "2024-01-01 12:00:00"
        paths = ["/path/to/file1.txt", "/path/to/file2.txt"]
        
        _insert_share(self.conn, share_id, created, paths)
        
        shares = _load_shares(self.conn)
        assert share_id in shares
        assert shares[share_id]["created"] == created
        assert shares[share_id]["paths"] == paths
    
    def test_insert_share_update_existing(self):
        """Test updating an existing share"""
        share_id = "test_share_123"
        created1 = "2024-01-01 12:00:00"
        paths1 = ["/path/to/file1.txt"]
        
        _insert_share(self.conn, share_id, created1, paths1)
        
        # Update the share
        created2 = "2024-01-02 12:00:00"
        paths2 = ["/path/to/file2.txt", "/path/to/file3.txt"]
        
        _insert_share(self.conn, share_id, created2, paths2)
        
        shares = _load_shares(self.conn)
        assert len(shares) == 1
        assert shares[share_id]["created"] == created2
        assert shares[share_id]["paths"] == paths2
    
    def test_delete_share(self):
        """Test deleting a share"""
        share_id = "test_share_123"
        created = "2024-01-01 12:00:00"
        paths = ["/path/to/file1.txt"]
        
        _insert_share(self.conn, share_id, created, paths)
        
        # Verify share exists
        shares = _load_shares(self.conn)
        assert share_id in shares
        
        # Delete share
        _delete_share(self.conn, share_id)
        
        # Verify share is deleted
        shares = _load_shares(self.conn)
        assert share_id not in shares
    
    def test_delete_nonexistent_share(self):
        """Test deleting a non-existent share"""
        # Should not raise exception
        _delete_share(self.conn, "nonexistent_share")
    
    def test_load_shares_invalid_json(self):
        """Test loading shares with invalid JSON in paths"""
        # Insert invalid JSON directly
        self.conn.execute(
            "INSERT INTO shares (id, created, paths) VALUES (?, ?, ?)",
            ("test_share", "2024-01-01", "invalid_json")
        )
        self.conn.commit()
        
        shares = _load_shares(self.conn)
        assert "test_share" in shares
        assert shares["test_share"]["paths"] == []
    
    def test_load_shares_database_error(self):
        """Test loading shares when database error occurs"""
        # Close connection to simulate error
        self.conn.close()
        
        shares = _load_shares(self.conn)
        assert shares == {}
    
    def test_insert_share_database_error(self):
        """Test inserting share when database error occurs"""
        # Close connection to simulate error
        self.conn.close()
        
        # Should not raise exception
        _insert_share(self.conn, "test", "2024-01-01", ["/path"])
    
    def test_delete_share_database_error(self):
        """Test deleting share when database error occurs"""
        # Close connection to simulate error
        self.conn.close()
        
        # Should not raise exception
        _delete_share(self.conn, "test")


class TestFeatureFlagHelpers:
    """Test feature flag helper functions"""
    
    @patch('aird.main.DB_CONN', None)
    def test_get_current_feature_flags_no_db(self):
        """Test getting feature flags when no database connection"""
        flags = get_current_feature_flags()
        assert flags == FEATURE_FLAGS
    
    @patch('aird.main.DB_CONN')
    @patch('aird.main._load_feature_flags')
    def test_get_current_feature_flags_with_db(self, mock_load, mock_db):
        """Test getting feature flags with database connection"""
        mock_load.return_value = {"file_upload": False, "new_feature": True}
        
        flags = get_current_feature_flags()
        
        # Should merge with defaults
        expected = FEATURE_FLAGS.copy()
        expected["file_upload"] = False
        expected["new_feature"] = True
        
        assert flags == expected
        mock_load.assert_called_once_with(mock_db)
    
    @patch('aird.main.DB_CONN')
    @patch('aird.main._load_feature_flags', side_effect=Exception("DB Error"))
    def test_get_current_feature_flags_db_error(self, mock_load, mock_db):
        """Test getting feature flags when database error occurs"""
        flags = get_current_feature_flags()
        assert flags == FEATURE_FLAGS
    
    @patch('aird.main.get_current_feature_flags')
    def test_is_feature_enabled_true(self, mock_get_flags):
        """Test is_feature_enabled when feature is enabled"""
        mock_get_flags.return_value = {"test_feature": True}
        
        result = is_feature_enabled("test_feature")
        assert result is True
    
    @patch('aird.main.get_current_feature_flags')
    def test_is_feature_enabled_false(self, mock_get_flags):
        """Test is_feature_enabled when feature is disabled"""
        mock_get_flags.return_value = {"test_feature": False}
        
        result = is_feature_enabled("test_feature")
        assert result is False
    
    @patch('aird.main.get_current_feature_flags')
    def test_is_feature_enabled_missing_default_false(self, mock_get_flags):
        """Test is_feature_enabled for missing feature with default False"""
        mock_get_flags.return_value = {}
        
        result = is_feature_enabled("missing_feature", default=False)
        assert result is False
    
    @patch('aird.main.get_current_feature_flags')
    def test_is_feature_enabled_missing_default_true(self, mock_get_flags):
        """Test is_feature_enabled for missing feature with default True"""
        mock_get_flags.return_value = {}
        
        result = is_feature_enabled("missing_feature", default=True)
        assert result is True