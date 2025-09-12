"""
Unit tests for database functions in aird.main module.

These tests cover database initialization, feature flags management,
shares management, and related database operations.
"""

import sqlite3
import json
import pytest
from unittest.mock import patch, MagicMock

# Import with error handling for missing module
try:
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
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False


@pytest.mark.database
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestDatabaseInit:
    """Test database initialization functionality"""
    
    def test_init_db_creates_tables(self):
        """Test that _init_db creates required tables with correct schema"""
        # Use in-memory database for testing
        conn = sqlite3.connect(":memory:")
        
        _init_db(conn)
        
        # Check that feature_flags table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='feature_flags'"
        )
        assert cursor.fetchone() is not None, "feature_flags table not created"
        
        # Check that shares table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shares'"
        )
        assert cursor.fetchone() is not None, "shares table not created"
        
        # Check feature_flags table structure
        cursor = conn.execute("PRAGMA table_info(feature_flags)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "key" in columns, "feature_flags table missing 'key' column"
        assert "value" in columns, "feature_flags table missing 'value' column"
        assert columns["key"] == "TEXT", "key column should be TEXT type"
        assert columns["value"] == "INTEGER", "value column should be INTEGER type"
        
        # Check shares table structure
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "id" in columns, "shares table missing 'id' column"
        assert "created" in columns, "shares table missing 'created' column"
        assert "paths" in columns, "shares table missing 'paths' column"
        
        conn.close()
    
    def test_init_db_idempotent(self):
        """Test that _init_db can be called multiple times safely"""
        conn = sqlite3.connect(":memory:")
        
        # Initialize database twice
        _init_db(conn)
        _init_db(conn)
        
        # Should still work and have correct tables
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        )
        table_count = cursor.fetchone()[0]
        assert table_count >= 2, "Tables missing after double initialization"
        
        conn.close()
    
    def test_init_db_with_existing_data(self):
        """Test that _init_db preserves existing data"""
        conn = sqlite3.connect(":memory:")
        
        # Initialize and add some data
        _init_db(conn)
        conn.execute(
            "INSERT INTO feature_flags (key, value) VALUES (?, ?)",
            ("test_flag", 1)
        )
        conn.commit()
        
        # Initialize again
        _init_db(conn)
        
        # Data should still exist
        cursor = conn.execute(
            "SELECT value FROM feature_flags WHERE key = ?",
            ("test_flag",)
        )
        result = cursor.fetchone()
        assert result is not None, "Existing data lost after re-initialization"
        assert result[0] == 1, "Existing data corrupted"
        
        conn.close()


@pytest.mark.database
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestFeatureFlags:
    """Test feature flags database operations"""
    
    @pytest.fixture
    def db_conn(self):
        """Provide a fresh database connection for each test"""
        conn = sqlite3.connect(":memory:")
        _init_db(conn)
        yield conn
        conn.close()
    
    def test_load_feature_flags_empty(self, db_conn):
        """Test loading feature flags from empty database"""
        flags = _load_feature_flags(db_conn)
        assert flags == {}, "Empty database should return empty dict"
    
    def test_save_and_load_feature_flags(self, db_conn):
        """Test saving and loading feature flags"""
        test_flags = {
            "file_upload": True,
            "file_delete": False,
            "file_edit": True,
            "new_feature": False
        }
        
        _save_feature_flags(db_conn, test_flags)
        loaded_flags = _load_feature_flags(db_conn)
        
        assert loaded_flags == test_flags, "Loaded flags don't match saved flags"
    
    def test_save_feature_flags_update_existing(self, db_conn):
        """Test updating existing feature flags"""
        # Insert initial flags
        initial_flags = {"file_upload": True, "file_delete": False}
        _save_feature_flags(db_conn, initial_flags)
        
        # Update flags with new values and additional flags
        updated_flags = {
            "file_upload": False,  # Changed
            "file_delete": True,   # Changed
            "file_edit": True,     # New
            "compression": False   # New
        }
        _save_feature_flags(db_conn, updated_flags)
        
        loaded_flags = _load_feature_flags(db_conn)
        assert loaded_flags == updated_flags, "Updated flags not saved correctly"
    
    def test_save_feature_flags_partial_update(self, db_conn):
        """Test partial update of feature flags"""
        # Set initial flags
        initial_flags = {
            "file_upload": True,
            "file_delete": False,
            "file_edit": True
        }
        _save_feature_flags(db_conn, initial_flags)
        
        # Update only some flags
        partial_update = {"file_upload": False}
        _save_feature_flags(db_conn, partial_update)
        
        loaded_flags = _load_feature_flags(db_conn)
        # Should have all flags with the updated value for file_upload
        expected_flags = {
            "file_upload": False,  # Updated
            "file_delete": False,  # Unchanged
            "file_edit": True      # Unchanged
        }
        assert loaded_flags == expected_flags
    
    def test_save_feature_flags_boolean_conversion(self, db_conn):
        """Test that boolean values are properly converted"""
        test_flags = {
            "true_flag": True,
            "false_flag": False,
            "truthy_flag": 1,
            "falsy_flag": 0
        }
        
        _save_feature_flags(db_conn, test_flags)
        loaded_flags = _load_feature_flags(db_conn)
        
        # All values should be proper booleans
        assert loaded_flags["true_flag"] is True
        assert loaded_flags["false_flag"] is False
        assert loaded_flags["truthy_flag"] is True
        assert loaded_flags["falsy_flag"] is False
    
    def test_load_feature_flags_database_error(self):
        """Test loading feature flags when database error occurs"""
        # Use closed connection to simulate error
        conn = sqlite3.connect(":memory:")
        conn.close()
        
        flags = _load_feature_flags(conn)
        assert flags == {}, "Should return empty dict on database error"
    
    def test_save_feature_flags_database_error(self):
        """Test saving feature flags when database error occurs"""
        # Use closed connection to simulate error
        conn = sqlite3.connect(":memory:")
        conn.close()
        
        test_flags = {"file_upload": True}
        # Should not raise exception
        try:
            _save_feature_flags(conn, test_flags)
        except Exception as e:
            pytest.fail(f"save_feature_flags raised exception: {e}")
    
    def test_feature_flags_with_special_characters(self, db_conn):
        """Test feature flags with special characters in keys"""
        special_flags = {
            "flag-with-dashes": True,
            "flag_with_underscores": False,
            "flag with spaces": True,  # Unusual but should work
            "flagWithCamelCase": False
        }
        
        _save_feature_flags(db_conn, special_flags)
        loaded_flags = _load_feature_flags(db_conn)
        
        assert loaded_flags == special_flags


@pytest.mark.database
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestShares:
    """Test shares database operations"""
    
    @pytest.fixture
    def db_conn(self):
        """Provide a fresh database connection for each test"""
        conn = sqlite3.connect(":memory:")
        _init_db(conn)
        yield conn
        conn.close()
    
    def test_load_shares_empty(self, db_conn):
        """Test loading shares from empty database"""
        shares = _load_shares(db_conn)
        assert shares == {}, "Empty database should return empty dict"
    
    def test_insert_and_load_share(self, db_conn):
        """Test inserting and loading a share"""
        share_id = "test_share_123"
        created = "2024-01-01 12:00:00"
        paths = ["/path/to/file1.txt", "/path/to/file2.txt"]
        
        _insert_share(db_conn, share_id, created, paths)
        
        shares = _load_shares(db_conn)
        assert share_id in shares, "Share not found in loaded shares"
        assert shares[share_id]["created"] == created
        assert shares[share_id]["paths"] == paths
    
    def test_insert_share_update_existing(self, db_conn):
        """Test updating an existing share"""
        share_id = "test_share_123"
        created1 = "2024-01-01 12:00:00"
        paths1 = ["/path/to/file1.txt"]
        
        _insert_share(db_conn, share_id, created1, paths1)
        
        # Update the share
        created2 = "2024-01-02 12:00:00"
        paths2 = ["/path/to/file2.txt", "/path/to/file3.txt"]
        
        _insert_share(db_conn, share_id, created2, paths2)
        
        shares = _load_shares(db_conn)
        assert len(shares) == 1, "Should have only one share after update"
        assert shares[share_id]["created"] == created2
        assert shares[share_id]["paths"] == paths2
    
    def test_insert_multiple_shares(self, db_conn):
        """Test inserting multiple different shares"""
        shares_data = [
            ("share1", "2024-01-01 10:00:00", ["/file1.txt"]),
            ("share2", "2024-01-01 11:00:00", ["/file2.txt", "/file3.txt"]),
            ("share3", "2024-01-01 12:00:00", ["/dir1/file4.txt"])
        ]
        
        for share_id, created, paths in shares_data:
            _insert_share(db_conn, share_id, created, paths)
        
        shares = _load_shares(db_conn)
        assert len(shares) == 3, "Should have all three shares"
        
        for share_id, created, paths in shares_data:
            assert share_id in shares
            assert shares[share_id]["created"] == created
            assert shares[share_id]["paths"] == paths
    
    def test_delete_share(self, db_conn):
        """Test deleting a share"""
        share_id = "test_share_123"
        created = "2024-01-01 12:00:00"
        paths = ["/path/to/file1.txt"]
        
        _insert_share(db_conn, share_id, created, paths)
        
        # Verify share exists
        shares = _load_shares(db_conn)
        assert share_id in shares, "Share should exist before deletion"
        
        # Delete share
        _delete_share(db_conn, share_id)
        
        # Verify share is deleted
        shares = _load_shares(db_conn)
        assert share_id not in shares, "Share should be deleted"
    
    def test_delete_nonexistent_share(self, db_conn):
        """Test deleting a non-existent share"""
        # Should not raise exception
        try:
            _delete_share(db_conn, "nonexistent_share")
        except Exception as e:
            pytest.fail(f"delete_share raised exception for non-existent share: {e}")
    
    def test_delete_share_preserves_others(self, db_conn):
        """Test that deleting one share doesn't affect others"""
        # Insert multiple shares
        shares_data = [
            ("share1", "2024-01-01 10:00:00", ["/file1.txt"]),
            ("share2", "2024-01-01 11:00:00", ["/file2.txt"]),
            ("share3", "2024-01-01 12:00:00", ["/file3.txt"])
        ]
        
        for share_id, created, paths in shares_data:
            _insert_share(db_conn, share_id, created, paths)
        
        # Delete middle share
        _delete_share(db_conn, "share2")
        
        shares = _load_shares(db_conn)
        assert len(shares) == 2, "Should have 2 shares remaining"
        assert "share1" in shares
        assert "share2" not in shares
        assert "share3" in shares
    
    def test_load_shares_invalid_json(self, db_conn):
        """Test loading shares with invalid JSON in paths"""
        # Insert invalid JSON directly into database
        db_conn.execute(
            "INSERT INTO shares (id, created, paths) VALUES (?, ?, ?)",
            ("test_share", "2024-01-01", "invalid_json")
        )
        db_conn.commit()
        
        shares = _load_shares(db_conn)
        assert "test_share" in shares, "Share with invalid JSON should still be loaded"
        assert shares["test_share"]["paths"] == [], "Invalid JSON should result in empty paths"
    
    def test_load_shares_empty_json(self, db_conn):
        """Test loading shares with empty JSON array"""
        # Insert empty JSON array
        db_conn.execute(
            "INSERT INTO shares (id, created, paths) VALUES (?, ?, ?)",
            ("test_share", "2024-01-01", "[]")
        )
        db_conn.commit()
        
        shares = _load_shares(db_conn)
        assert "test_share" in shares
        assert shares["test_share"]["paths"] == []
    
    def test_load_shares_database_error(self):
        """Test loading shares when database error occurs"""
        # Use closed connection to simulate error
        conn = sqlite3.connect(":memory:")
        conn.close()
        
        shares = _load_shares(conn)
        assert shares == {}, "Should return empty dict on database error"
    
    def test_insert_share_database_error(self):
        """Test inserting share when database error occurs"""
        # Use closed connection to simulate error
        conn = sqlite3.connect(":memory:")
        conn.close()
        
        # Should not raise exception
        try:
            _insert_share(conn, "test", "2024-01-01", ["/path"])
        except Exception as e:
            pytest.fail(f"insert_share raised exception: {e}")
    
    def test_delete_share_database_error(self):
        """Test deleting share when database error occurs"""
        # Use closed connection to simulate error
        conn = sqlite3.connect(":memory:")
        conn.close()
        
        # Should not raise exception
        try:
            _delete_share(conn, "test")
        except Exception as e:
            pytest.fail(f"delete_share raised exception: {e}")
    
    def test_share_paths_serialization(self, db_conn):
        """Test that share paths are properly serialized and deserialized"""
        share_id = "serialization_test"
        created = "2024-01-01 12:00:00"
        
        # Test with various path formats
        test_cases = [
            [],  # Empty paths
            ["/simple/path.txt"],  # Single path
            ["/path1.txt", "/path2.txt", "/path3.txt"],  # Multiple paths
            ["/path with spaces.txt"],  # Spaces in path
            ["/path/with/unicode/émojis🚀.txt"],  # Unicode characters
            ["/very/long/path/" + "subdir/" * 20 + "file.txt"]  # Very long path
        ]
        
        for i, paths in enumerate(test_cases):
            test_share_id = f"{share_id}_{i}"
            _insert_share(db_conn, test_share_id, created, paths)
            
            shares = _load_shares(db_conn)
            assert test_share_id in shares
            assert shares[test_share_id]["paths"] == paths


@pytest.mark.database
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestFeatureFlagHelpers:
    """Test feature flag helper functions"""
    
    def test_get_current_feature_flags_no_db(self):
        """Test getting feature flags when no database connection"""
        with patch('aird.main.DB_CONN', None):
            flags = get_current_feature_flags()
            assert flags == FEATURE_FLAGS, "Should return default flags when no DB"
    
    def test_get_current_feature_flags_with_db(self):
        """Test getting feature flags with database connection"""
        mock_db = MagicMock()
        
        with patch('aird.main.DB_CONN', mock_db), \
             patch('aird.main._load_feature_flags') as mock_load:
            
            # Mock database returning some flags
            mock_load.return_value = {"file_upload": False, "new_feature": True}
            
            flags = get_current_feature_flags()
            
            # Should merge with defaults
            expected = FEATURE_FLAGS.copy()
            expected.update({"file_upload": False, "new_feature": True})
            
            assert flags == expected
            mock_load.assert_called_once_with(mock_db)
    
    def test_get_current_feature_flags_db_error(self):
        """Test getting feature flags when database error occurs"""
        mock_db = MagicMock()
        
        with patch('aird.main.DB_CONN', mock_db), \
             patch('aird.main._load_feature_flags', side_effect=Exception("DB Error")):
            
            flags = get_current_feature_flags()
            assert flags == FEATURE_FLAGS, "Should return defaults on DB error"
    
    def test_is_feature_enabled_true(self):
        """Test is_feature_enabled when feature is enabled"""
        with patch('aird.main.get_current_feature_flags') as mock_get_flags:
            mock_get_flags.return_value = {"test_feature": True}
            
            result = is_feature_enabled("test_feature")
            assert result is True
    
    def test_is_feature_enabled_false(self):
        """Test is_feature_enabled when feature is disabled"""
        with patch('aird.main.get_current_feature_flags') as mock_get_flags:
            mock_get_flags.return_value = {"test_feature": False}
            
            result = is_feature_enabled("test_feature")
            assert result is False
    
    def test_is_feature_enabled_missing_default_false(self):
        """Test is_feature_enabled for missing feature with default False"""
        with patch('aird.main.get_current_feature_flags') as mock_get_flags:
            mock_get_flags.return_value = {}
            
            result = is_feature_enabled("missing_feature", default=False)
            assert result is False
    
    def test_is_feature_enabled_missing_default_true(self):
        """Test is_feature_enabled for missing feature with default True"""
        with patch('aird.main.get_current_feature_flags') as mock_get_flags:
            mock_get_flags.return_value = {}
            
            result = is_feature_enabled("missing_feature", default=True)
            assert result is True
    
    def test_is_feature_enabled_no_default_specified(self):
        """Test is_feature_enabled for missing feature with no default"""
        with patch('aird.main.get_current_feature_flags') as mock_get_flags:
            mock_get_flags.return_value = {}
            
            result = is_feature_enabled("missing_feature")
            # Should return False as the implicit default
            assert result is False
    
    @pytest.mark.parametrize("flag_value,expected", [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),  # Non-empty strings are truthy
        ("false", True),  # Non-empty strings are truthy (changed expectation)
        (None, False),
        ("", False),
    ])
    def test_is_feature_enabled_value_conversion(self, flag_value, expected):
        """Test is_feature_enabled with various value types"""
        with patch('aird.main.get_current_feature_flags') as mock_get_flags:
            mock_get_flags.return_value = {"test_feature": flag_value}
            
            result = is_feature_enabled("test_feature")
            assert bool(result) == expected


@pytest.mark.integration
@pytest.mark.database
@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird.main module not available")
class TestDatabaseIntegration:
    """Integration tests for database operations working together"""
    
    def test_full_database_lifecycle(self):
        """Test complete database operations lifecycle"""
        conn = sqlite3.connect(":memory:")
        
        try:
            # Initialize database
            _init_db(conn)
            
            # Test feature flags lifecycle
            initial_flags = {"feature1": True, "feature2": False}
            _save_feature_flags(conn, initial_flags)
            
            loaded_flags = _load_feature_flags(conn)
            assert loaded_flags == initial_flags
            
            # Test shares lifecycle
            share_id = "test_share"
            created = "2024-01-01 12:00:00"
            paths = ["/file1.txt", "/file2.txt"]
            
            _insert_share(conn, share_id, created, paths)
            
            shares = _load_shares(conn)
            assert share_id in shares
            assert shares[share_id]["paths"] == paths
            
            # Update both flags and shares
            updated_flags = {"feature1": False, "feature3": True}
            _save_feature_flags(conn, updated_flags)
            
            updated_paths = ["/file1.txt", "/file3.txt", "/file4.txt"]
            _insert_share(conn, share_id, created, updated_paths)
            
            # Verify both updates
            final_flags = _load_feature_flags(conn)
            final_shares = _load_shares(conn)
            
            # Should have all flags: original feature2 + updated feature1 + new feature3
            expected_final_flags = {"feature1": False, "feature2": False, "feature3": True}
            assert final_flags == expected_final_flags
            assert final_shares[share_id]["paths"] == updated_paths
            
            # Clean up
            _delete_share(conn, share_id)
            final_shares = _load_shares(conn)
            assert share_id not in final_shares
            
        finally:
            conn.close()
    
    def test_concurrent_database_operations(self):
        """Test that database operations work correctly with concurrent access"""
        conn = sqlite3.connect(":memory:")
        
        try:
            _init_db(conn)
            
            # Simulate concurrent flag updates
            flags1 = {"feature1": True}
            flags2 = {"feature2": False}
            
            _save_feature_flags(conn, flags1)
            _save_feature_flags(conn, flags2)
            
            # Both flags should be present since _save_feature_flags preserves existing flags
            final_flags = _load_feature_flags(conn)
            expected_flags = {"feature1": True, "feature2": False}
            assert final_flags == expected_flags
            
            # Simulate concurrent share operations
            _insert_share(conn, "share1", "2024-01-01", ["/file1"])
            _insert_share(conn, "share2", "2024-01-02", ["/file2"])
            
            shares = _load_shares(conn)
            assert len(shares) == 2
            assert "share1" in shares
            assert "share2" in shares
            
        finally:
            conn.close()