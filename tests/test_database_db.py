"""Tests for aird/database/db.py"""

import pytest
import sqlite3
import os
import tempfile
from unittest.mock import patch

from aird.database.db import (
    get_data_dir,
    init_db,
    set_db_conn,
    get_db_conn,
    get_db_path,
)


class TestGetDataDir:
    """Tests for get_data_dir function"""
    
    def test_returns_string(self):
        """Test that get_data_dir returns a string"""
        result = get_data_dir()
        assert isinstance(result, str)
    
    def test_directory_exists_or_created(self):
        """Test that the directory exists after calling get_data_dir"""
        result = get_data_dir()
        assert os.path.exists(result)
    
    @patch('os.name', 'nt')
    @patch('os.environ.get')
    def test_windows_localappdata(self, mock_env_get):
        """Test Windows LOCALAPPDATA path"""
        mock_env_get.side_effect = lambda k, d=None: 'C:\\Users\\Test\\AppData\\Local' if k == 'LOCALAPPDATA' else d
        
        with patch('os.makedirs'):
            result = get_data_dir()
            assert 'aird' in result
    
    @patch('sys.platform', 'darwin')
    @patch('os.name', 'posix')
    @patch('os.path.expanduser')
    def test_macos_path(self, mock_expand):
        """Test macOS path"""
        mock_expand.return_value = '/Users/test/Library/Application Support'
        
        with patch('os.makedirs'):
            result = get_data_dir()
            assert 'aird' in result
    
    @patch('sys.platform', 'linux')
    @patch('os.name', 'posix')
    @patch('os.environ.get')
    @patch('os.path.expanduser')
    def test_linux_xdg_data_home(self, mock_expand, mock_env_get):
        """Test Linux with XDG_DATA_HOME"""
        mock_env_get.side_effect = lambda k, d=None: '/home/test/.local/share' if k == 'XDG_DATA_HOME' else d
        mock_expand.return_value = '/home/test/.local/share'
        
        with patch('os.makedirs'):
            result = get_data_dir()
            assert 'aird' in result


class TestInitDb:
    """Tests for init_db function"""
    
    def test_creates_feature_flags_table(self):
        """Test that feature_flags table is created"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='feature_flags'"
        )
        assert cursor.fetchone() is not None
        conn.close()
    
    def test_creates_shares_table(self):
        """Test that shares table is created"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shares'"
        )
        assert cursor.fetchone() is not None
        conn.close()
    
    def test_creates_users_table(self):
        """Test that users table is created"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        )
        assert cursor.fetchone() is not None
        conn.close()
    
    def test_creates_ldap_configs_table(self):
        """Test that ldap_configs table is created"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ldap_configs'"
        )
        assert cursor.fetchone() is not None
        conn.close()
    
    def test_creates_ldap_sync_log_table(self):
        """Test that ldap_sync_log table is created"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ldap_sync_log'"
        )
        assert cursor.fetchone() is not None
        conn.close()
    
    def test_shares_table_has_all_columns(self):
        """Test that shares table has all required columns after migration"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        expected_columns = [
            'id', 'created', 'paths', 'allowed_users', 'secret_token',
            'share_type', 'allow_list', 'avoid_list', 'expiry_date'
        ]
        for col in expected_columns:
            assert col in columns, f"Column {col} not found in shares table"
        
        conn.close()
    
    def test_users_table_has_all_columns(self):
        """Test that users table has all required columns"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]
        
        expected_columns = [
            'id', 'username', 'password_hash', 'role', 'created_at', 'active', 'last_login'
        ]
        for col in expected_columns:
            assert col in columns, f"Column {col} not found in users table"
        
        conn.close()
    
    def test_migration_adds_missing_columns(self):
        """Test that migration adds missing columns to existing shares table"""
        conn = sqlite3.connect(":memory:")
        
        # Create old schema
        conn.execute("""
            CREATE TABLE shares (
                id TEXT PRIMARY KEY,
                created TEXT NOT NULL,
                paths TEXT NOT NULL
            )
        """)
        conn.commit()
        
        # Run migration
        init_db(conn)
        
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Check that new columns were added
        assert 'allowed_users' in columns
        assert 'secret_token' in columns
        assert 'share_type' in columns
        assert 'allow_list' in columns
        assert 'avoid_list' in columns
        assert 'expiry_date' in columns
        
        conn.close()
    
    def test_idempotent_init(self):
        """Test that init_db can be called multiple times safely"""
        conn = sqlite3.connect(":memory:")
        
        # Call init_db multiple times
        init_db(conn)
        init_db(conn)
        init_db(conn)
        
        # Should not raise any errors
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        assert 'feature_flags' in tables
        assert 'shares' in tables
        assert 'users' in tables
        
        conn.close()


class TestSetDbConn:
    """Tests for set_db_conn function"""
    
    def test_set_connection(self):
        """Test setting database connection"""
        conn = sqlite3.connect(":memory:")
        
        set_db_conn(conn, "test.db")
        
        assert get_db_conn() == conn
        assert get_db_path() == "test.db"
        
        conn.close()
    
    def test_set_connection_without_path(self):
        """Test setting connection without path"""
        conn = sqlite3.connect(":memory:")
        
        set_db_conn(conn)
        
        assert get_db_conn() == conn
        assert get_db_path() is None
        
        conn.close()


class TestGetDbConn:
    """Tests for get_db_conn function"""
    
    def test_get_connection_returns_set_connection(self):
        """Test that get_db_conn returns the set connection"""
        conn = sqlite3.connect(":memory:")
        set_db_conn(conn)
        
        result = get_db_conn()
        
        assert result == conn
        conn.close()


class TestGetDbPath:
    """Tests for get_db_path function"""
    
    def test_get_path_returns_set_path(self):
        """Test that get_db_path returns the set path"""
        conn = sqlite3.connect(":memory:")
        set_db_conn(conn, "/path/to/db.sqlite3")
        
        result = get_db_path()
        
        assert result == "/path/to/db.sqlite3"
        conn.close()


class TestDatabaseIntegration:
    """Integration tests for database operations"""
    
    def test_full_database_workflow(self):
        """Test a full database workflow"""
        # Create connection
        conn = sqlite3.connect(":memory:")
        
        # Initialize database
        init_db(conn)
        
        # Set as global connection
        set_db_conn(conn, ":memory:")
        
        # Verify connection is set
        assert get_db_conn() is not None
        
        # Insert test data
        conn.execute(
            "INSERT INTO feature_flags (key, value) VALUES (?, ?)",
            ("test_flag", 1)
        )
        conn.commit()
        
        # Verify data
        cursor = conn.execute("SELECT value FROM feature_flags WHERE key = ?", ("test_flag",))
        row = cursor.fetchone()
        assert row[0] == 1
        
        conn.close()
    
    def test_shares_table_operations(self):
        """Test basic shares table operations"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        import json
        
        # Insert share
        conn.execute(
            """INSERT INTO shares (id, created, paths, allowed_users, secret_token, share_type) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("share123", "2024-01-01", json.dumps(["/path/file.txt"]), 
             json.dumps(["user1"]), "token123", "static")
        )
        conn.commit()
        
        # Retrieve share
        cursor = conn.execute("SELECT * FROM shares WHERE id = ?", ("share123",))
        row = cursor.fetchone()
        
        assert row is not None
        assert row[0] == "share123"
        
        conn.close()
    
    def test_users_table_operations(self):
        """Test basic users table operations"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        
        # Insert user
        conn.execute(
            """INSERT INTO users (username, password_hash, role, created_at) 
               VALUES (?, ?, ?, ?)""",
            ("testuser", "hash123", "user", "2024-01-01T00:00:00")
        )
        conn.commit()
        
        # Retrieve user
        cursor = conn.execute("SELECT * FROM users WHERE username = ?", ("testuser",))
        row = cursor.fetchone()
        
        assert row is not None
        assert row[1] == "testuser"
        
        conn.close()
