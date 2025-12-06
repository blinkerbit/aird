"""Tests for aird/database/shares.py"""

import pytest
import sqlite3
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from aird.database.shares import (
    insert_share,
    delete_share,
    update_share,
    get_share_by_id,
    get_all_shares,
    get_shares_for_path,
    is_share_expired,
    cleanup_expired_shares
)


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database for testing"""
    conn = sqlite3.connect(":memory:")
    conn.execute('''
        CREATE TABLE shares (
            id TEXT PRIMARY KEY,
            created TEXT NOT NULL,
            paths TEXT NOT NULL,
            allowed_users TEXT,
            secret_token TEXT,
            share_type TEXT DEFAULT 'static',
            allow_list TEXT,
            avoid_list TEXT,
            expiry_date TEXT
        )
    ''')
    conn.commit()
    yield conn
    conn.close()


class TestInsertShare:
    """Tests for insert_share function"""
    
    def test_insert_basic_share(self, db_conn):
        """Test inserting a basic share"""
        result = insert_share(
            db_conn,
            sid="share123",
            created="2024-01-01T00:00:00",
            paths=["/path/to/file.txt"]
        )
        
        assert result is True
        
        # Verify in database
        cursor = db_conn.execute("SELECT id, paths FROM shares WHERE id = ?", ("share123",))
        row = cursor.fetchone()
        assert row[0] == "share123"
        assert json.loads(row[1]) == ["/path/to/file.txt"]
    
    def test_insert_share_with_all_options(self, db_conn):
        """Test inserting a share with all options"""
        result = insert_share(
            db_conn,
            sid="share456",
            created="2024-01-01T00:00:00",
            paths=["/path/file1.txt", "/path/file2.txt"],
            allowed_users=["user1", "user2"],
            secret_token="secret123",
            share_type="dynamic",
            allow_list=["*.txt"],
            avoid_list=["*.log"],
            expiry_date="2024-12-31T23:59:59"
        )
        
        assert result is True
        
        share = get_share_by_id(db_conn, "share456")
        assert share['allowed_users'] == ["user1", "user2"]
        assert share['secret_token'] == "secret123"
        assert share['share_type'] == "dynamic"
        assert share['allow_list'] == ["*.txt"]
        assert share['avoid_list'] == ["*.log"]
        assert share['expiry_date'] == "2024-12-31T23:59:59"
    
    def test_insert_share_replaces_existing(self, db_conn):
        """Test that inserting with same ID replaces existing share"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/old/path.txt"])
        insert_share(db_conn, "share123", "2024-01-02T00:00:00", ["/new/path.txt"])
        
        share = get_share_by_id(db_conn, "share123")
        assert share['paths'] == ["/new/path.txt"]
    
    def test_insert_share_exception_returns_false(self):
        """Test that database exceptions return False"""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=Exception("Database error"))
        
        result = insert_share(mock_conn, "share123", "2024-01-01", ["/path"])
        assert result is False


class TestDeleteShare:
    """Tests for delete_share function"""
    
    def test_delete_existing_share(self, db_conn):
        """Test deleting an existing share"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path/file.txt"])
        
        with patch('aird.database.shares.remove_share_cloud_dir'):
            delete_share(db_conn, "share123")
        
        assert get_share_by_id(db_conn, "share123") is None
    
    def test_delete_nonexistent_share(self, db_conn):
        """Test deleting a non-existent share doesn't raise error"""
        with patch('aird.database.shares.remove_share_cloud_dir'):
            delete_share(db_conn, "nonexistent")  # Should not raise
    
    def test_delete_share_calls_remove_cloud_dir(self, db_conn):
        """Test that delete_share calls remove_share_cloud_dir"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path/file.txt"])
        
        with patch('aird.database.shares.remove_share_cloud_dir') as mock_remove:
            delete_share(db_conn, "share123")
            mock_remove.assert_called_once_with("share123")


class TestUpdateShare:
    """Tests for update_share function"""
    
    def test_update_share_type(self, db_conn):
        """Test updating share type"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path/file.txt"])
        
        result = update_share(db_conn, "share123", share_type="dynamic")
        
        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share['share_type'] == "dynamic"
    
    def test_update_disable_token(self, db_conn):
        """Test disabling token"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"], secret_token="oldtoken")
        
        result = update_share(db_conn, "share123", disable_token=True)
        
        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share['secret_token'] is None
    
    def test_update_secret_token(self, db_conn):
        """Test updating secret token"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"])
        
        result = update_share(db_conn, "share123", secret_token="newtoken123")
        
        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share['secret_token'] == "newtoken123"
    
    def test_update_generates_token_when_none_provided(self, db_conn):
        """Test that token is generated when disable_token=False and no token provided"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"])
        
        result = update_share(db_conn, "share123", disable_token=False)
        
        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share['secret_token'] is not None
        assert len(share['secret_token']) > 0
    
    def test_update_allow_list(self, db_conn):
        """Test updating allow list"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"])
        
        result = update_share(db_conn, "share123", allow_list=["*.txt", "*.pdf"])
        
        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share['allow_list'] == ["*.txt", "*.pdf"]
    
    def test_update_avoid_list(self, db_conn):
        """Test updating avoid list"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"])
        
        result = update_share(db_conn, "share123", avoid_list=["*.log", "*.tmp"])
        
        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share['avoid_list'] == ["*.log", "*.tmp"]
    
    def test_update_expiry_date(self, db_conn):
        """Test updating expiry date"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"])
        
        result = update_share(db_conn, "share123", expiry_date="2025-01-01T00:00:00")
        
        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share['expiry_date'] == "2025-01-01T00:00:00"
    
    def test_update_no_changes_returns_false(self, db_conn):
        """Test that update with no valid fields returns False"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"])
        
        result = update_share(db_conn, "share123")
        
        assert result is False
    
    def test_update_legacy_allowed_users(self, db_conn):
        """Test updating legacy allowed_users field"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"])
        
        result = update_share(db_conn, "share123", allowed_users=["user1", "user2"])
        
        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share['allowed_users'] == ["user1", "user2"]


class TestGetShareById:
    """Tests for get_share_by_id function"""
    
    def test_get_existing_share(self, db_conn):
        """Test getting an existing share"""
        insert_share(
            db_conn, "share123", "2024-01-01T00:00:00", 
            ["/path/file.txt"], secret_token="token123"
        )
        
        share = get_share_by_id(db_conn, "share123")
        
        assert share is not None
        assert share['id'] == "share123"
        assert share['paths'] == ["/path/file.txt"]
        assert share['secret_token'] == "token123"
    
    def test_get_nonexistent_share(self, db_conn):
        """Test getting a non-existent share returns None"""
        share = get_share_by_id(db_conn, "nonexistent")
        assert share is None
    
    def test_get_share_returns_correct_structure(self, db_conn):
        """Test that returned share has correct structure"""
        insert_share(db_conn, "share123", "2024-01-01T00:00:00", ["/path"])
        
        share = get_share_by_id(db_conn, "share123")
        
        assert 'id' in share
        assert 'created' in share
        assert 'paths' in share
        assert 'allowed_users' in share
        assert 'secret_token' in share
        assert 'share_type' in share
        assert 'allow_list' in share
        assert 'avoid_list' in share
        assert 'expiry_date' in share


class TestGetAllShares:
    """Tests for get_all_shares function"""
    
    def test_get_all_shares_empty(self, db_conn):
        """Test getting shares when none exist"""
        result = get_all_shares(db_conn)
        assert result == {}
    
    def test_get_all_shares_multiple(self, db_conn):
        """Test getting multiple shares"""
        insert_share(db_conn, "share1", "2024-01-01T00:00:00", ["/path1"])
        insert_share(db_conn, "share2", "2024-01-02T00:00:00", ["/path2"])
        
        result = get_all_shares(db_conn)
        
        assert len(result) == 2
        assert "share1" in result
        assert "share2" in result
    
    def test_get_all_shares_structure(self, db_conn):
        """Test structure of returned shares"""
        insert_share(db_conn, "share1", "2024-01-01T00:00:00", ["/path1"], secret_token="token1")
        
        result = get_all_shares(db_conn)
        
        share = result["share1"]
        assert 'created' in share
        assert 'paths' in share
        assert 'allowed_users' in share
        assert 'secret_token' in share


class TestGetSharesForPath:
    """Tests for get_shares_for_path function"""
    
    def test_get_shares_for_existing_path(self, db_conn):
        """Test getting shares that contain a specific path"""
        insert_share(db_conn, "share1", "2024-01-01T00:00:00", ["/path/file1.txt", "/path/file2.txt"])
        insert_share(db_conn, "share2", "2024-01-02T00:00:00", ["/path/file1.txt"])
        insert_share(db_conn, "share3", "2024-01-03T00:00:00", ["/other/file.txt"])
        
        result = get_shares_for_path(db_conn, "/path/file1.txt")
        
        assert len(result) == 2
        ids = [s['id'] for s in result]
        assert "share1" in ids
        assert "share2" in ids
        assert "share3" not in ids
    
    def test_get_shares_for_nonexistent_path(self, db_conn):
        """Test getting shares for path that doesn't exist in any share"""
        insert_share(db_conn, "share1", "2024-01-01T00:00:00", ["/path/file.txt"])
        
        result = get_shares_for_path(db_conn, "/nonexistent/path.txt")
        
        assert result == []
    
    def test_get_shares_for_path_empty_db(self, db_conn):
        """Test getting shares when database is empty"""
        result = get_shares_for_path(db_conn, "/path/file.txt")
        assert result == []


class TestIsShareExpired:
    """Tests for is_share_expired function"""
    
    def test_no_expiry_date_not_expired(self):
        """Test that None expiry date means not expired"""
        assert is_share_expired(None) is False
        assert is_share_expired("") is False
    
    def test_future_expiry_not_expired(self):
        """Test that future expiry date is not expired"""
        future_date = (datetime.now() + timedelta(days=1)).isoformat()
        assert is_share_expired(future_date) is False
    
    def test_past_expiry_is_expired(self):
        """Test that past expiry date is expired"""
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        assert is_share_expired(past_date) is True
    
    def test_expiry_with_z_suffix(self):
        """Test expiry date with Z suffix"""
        past_date = (datetime.now() - timedelta(days=1)).isoformat() + "Z"
        assert is_share_expired(past_date) is True
    
    def test_invalid_expiry_format_not_expired(self):
        """Test that invalid date format returns False (not expired)"""
        assert is_share_expired("invalid-date") is False


class TestCleanupExpiredShares:
    """Tests for cleanup_expired_shares function"""
    
    def test_cleanup_removes_expired_shares(self, db_conn):
        """Test that cleanup removes expired shares"""
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        future_date = (datetime.now() + timedelta(days=1)).isoformat()
        
        insert_share(db_conn, "expired1", "2024-01-01T00:00:00", ["/path1"], expiry_date=past_date)
        insert_share(db_conn, "expired2", "2024-01-01T00:00:00", ["/path2"], expiry_date=past_date)
        insert_share(db_conn, "valid", "2024-01-01T00:00:00", ["/path3"], expiry_date=future_date)
        insert_share(db_conn, "no_expiry", "2024-01-01T00:00:00", ["/path4"])
        
        deleted_count = cleanup_expired_shares(db_conn)
        
        assert deleted_count == 2
        assert get_share_by_id(db_conn, "expired1") is None
        assert get_share_by_id(db_conn, "expired2") is None
        assert get_share_by_id(db_conn, "valid") is not None
        assert get_share_by_id(db_conn, "no_expiry") is not None
    
    def test_cleanup_empty_db(self, db_conn):
        """Test cleanup with empty database"""
        deleted_count = cleanup_expired_shares(db_conn)
        assert deleted_count == 0
    
    def test_cleanup_no_expired_shares(self, db_conn):
        """Test cleanup when no shares are expired"""
        future_date = (datetime.now() + timedelta(days=1)).isoformat()
        insert_share(db_conn, "valid", "2024-01-01T00:00:00", ["/path"], expiry_date=future_date)
        
        deleted_count = cleanup_expired_shares(db_conn)
        assert deleted_count == 0


class TestSharesWithDifferentSchemas:
    """Tests for backward compatibility with different database schemas"""
    
    def test_get_share_without_new_columns(self):
        """Test getting share from database without newer columns"""
        # Create a database with minimal schema (older version)
        conn = sqlite3.connect(":memory:")
        conn.execute('''
            CREATE TABLE shares (
                id TEXT PRIMARY KEY,
                created TEXT NOT NULL,
                paths TEXT NOT NULL,
                allowed_users TEXT
            )
        ''')
        conn.execute(
            "INSERT INTO shares (id, created, paths) VALUES (?, ?, ?)",
            ("share1", "2024-01-01", json.dumps(["/path"]))
        )
        conn.commit()
        
        share = get_share_by_id(conn, "share1")
        
        assert share is not None
        assert share['id'] == "share1"
        assert share['secret_token'] is None
        assert share['share_type'] == "static"
        assert share['allow_list'] == []
        assert share['avoid_list'] == []
        
        conn.close()
