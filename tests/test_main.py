"""Tests for aird/main.py"""

import pytest
import sqlite3
import tempfile
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Import functions from main.py
from aird.main import (
    _get_data_dir,
    _init_db,
    _load_feature_flags,
    _save_feature_flags,
    _is_share_expired,
    _cleanup_expired_shares,
    _hash_password,
    _verify_password,
    _create_user,
    _get_user_by_username,
    _get_all_users,
    _search_users,
    _update_user,
    _delete_user,
    _authenticate_user,
    _assign_admin_privileges,
    _extract_username_from_dn,
)


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database for testing"""
    conn = sqlite3.connect(":memory:")
    _init_db(conn)
    yield conn
    conn.close()


class TestGetDataDir:
    """Tests for _get_data_dir function"""
    
    def test_get_data_dir_windows(self):
        """Test data directory on Windows"""
        with patch('os.name', 'nt'), \
             patch('os.environ.get', side_effect=lambda k, d=None: 'C:\\Users\\Test\\AppData\\Local' if k == 'LOCALAPPDATA' else d), \
             patch('os.path.expanduser', return_value='C:\\Users\\Test\\AppData\\Local'), \
             patch('os.makedirs'), \
             patch('os.path.join', side_effect=lambda *args: '\\'.join(args)):
            result = _get_data_dir()
            assert 'aird' in result
    
    def test_get_data_dir_macos(self):
        """Test data directory on macOS"""
        with patch('os.name', 'posix'), \
             patch('sys.platform', 'darwin'), \
             patch('os.path.expanduser', return_value='/Users/test/Library/Application Support'), \
             patch('os.makedirs'), \
             patch('os.path.join', side_effect=lambda *args: '/'.join(args)):
            result = _get_data_dir()
            assert 'aird' in result
    
    def test_get_data_dir_linux(self):
        """Test data directory on Linux"""
        with patch('os.name', 'posix'), \
             patch('sys.platform', 'linux'), \
             patch('os.environ.get', side_effect=lambda k, d=None: '/home/test/.local/share' if k == 'XDG_DATA_HOME' else d), \
             patch('os.path.expanduser', return_value='/home/test/.local/share'), \
             patch('os.makedirs'), \
             patch('os.path.join', side_effect=lambda *args: '/'.join(args)):
            result = _get_data_dir()
            assert 'aird' in result
    
    def test_get_data_dir_fallback(self):
        """Test data directory fallback on exception"""
        with patch('os.name', 'nt'), \
             patch('os.environ.get', side_effect=Exception("Error")), \
             patch('os.getcwd', return_value='/current/dir'):
            result = _get_data_dir()
            assert result == '/current/dir'


class TestInitDb:
    """Tests for _init_db function"""
    
    def test_init_db_creates_tables(self, db_conn):
        """Test that _init_db creates all required tables"""
        cursor = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        assert 'feature_flags' in tables
        assert 'shares' in tables
        assert 'users' in tables
        assert 'ldap_configs' in tables
        assert 'ldap_sync_log' in tables
    
    def test_init_db_adds_missing_columns(self):
        """Test that _init_db adds missing columns to shares table"""
        conn = sqlite3.connect(":memory:")
        # Create shares table without new columns
        conn.execute("""
            CREATE TABLE shares (
                id TEXT PRIMARY KEY,
                created TEXT NOT NULL,
                paths TEXT NOT NULL
            )
        """)
        conn.commit()
        
        _init_db(conn)
        
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        assert 'allowed_users' in columns
        assert 'secret_token' in columns
        assert 'share_type' in columns
        assert 'allow_list' in columns
        assert 'avoid_list' in columns
        assert 'expiry_date' in columns
        
        conn.close()


class TestLoadFeatureFlags:
    """Tests for _load_feature_flags function"""
    
    def test_load_empty_flags(self, db_conn):
        """Test loading when no flags exist"""
        result = _load_feature_flags(db_conn)
        assert result == {}
    
    def test_load_existing_flags(self, db_conn):
        """Test loading existing feature flags"""
        db_conn.execute("INSERT INTO feature_flags (key, value) VALUES ('flag1', 1)")
        db_conn.execute("INSERT INTO feature_flags (key, value) VALUES ('flag2', 0)")
        db_conn.commit()
        
        result = _load_feature_flags(db_conn)
        assert result == {'flag1': True, 'flag2': False}
    
    def test_load_flags_exception_returns_empty(self):
        """Test that exceptions return empty dict"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database error")
        
        result = _load_feature_flags(mock_conn)
        assert result == {}


class TestSaveFeatureFlags:
    """Tests for _save_feature_flags function"""
    
    def test_save_new_flags(self, db_conn):
        """Test saving new feature flags"""
        flags = {'flag1': True, 'flag2': False}
        _save_feature_flags(db_conn, flags)
        
        result = _load_feature_flags(db_conn)
        assert result == flags
    
    def test_save_updates_existing_flags(self, db_conn):
        """Test that saving updates existing flags"""
        db_conn.execute("INSERT INTO feature_flags (key, value) VALUES ('flag1', 0)")
        db_conn.commit()
        
        _save_feature_flags(db_conn, {'flag1': True})
        
        result = _load_feature_flags(db_conn)
        assert result['flag1'] is True


class TestIsShareExpired:
    """Tests for _is_share_expired function"""
    
    def test_no_expiry_date_not_expired(self):
        """Test that None expiry date means not expired"""
        assert _is_share_expired(None) is False
        assert _is_share_expired("") is False
    
    def test_future_expiry_not_expired(self):
        """Test that future expiry date is not expired"""
        future_date = (datetime.now() + timedelta(days=1)).isoformat()
        assert _is_share_expired(future_date) is False
    
    def test_past_expiry_is_expired(self):
        """Test that past expiry date is expired"""
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        assert _is_share_expired(past_date) is True
    
    def test_expiry_with_z_suffix(self):
        """Test expiry date with Z suffix"""
        past_date = (datetime.now() - timedelta(days=1)).isoformat() + "Z"
        assert _is_share_expired(past_date) is True
    
    def test_invalid_expiry_format_not_expired(self):
        """Test that invalid date format returns False"""
        assert _is_share_expired("invalid-date") is False


class TestCleanupExpiredShares:
    """Tests for _cleanup_expired_shares function"""
    
    def test_cleanup_removes_expired_shares(self, db_conn):
        """Test that cleanup removes expired shares"""
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        future_date = (datetime.now() + timedelta(days=1)).isoformat()
        
        db_conn.execute(
            "INSERT INTO shares (id, created, paths, expiry_date) VALUES (?, ?, ?, ?)",
            ("expired1", "2024-01-01", '["/path1"]', past_date)
        )
        db_conn.execute(
            "INSERT INTO shares (id, created, paths, expiry_date) VALUES (?, ?, ?, ?)",
            ("expired2", "2024-01-01", '["/path2"]', past_date)
        )
        db_conn.execute(
            "INSERT INTO shares (id, created, paths, expiry_date) VALUES (?, ?, ?, ?)",
            ("valid", "2024-01-01", '["/path3"]', future_date)
        )
        db_conn.commit()
        
        deleted_count = _cleanup_expired_shares(db_conn)
        
        assert deleted_count == 2
        cursor = db_conn.execute("SELECT id FROM shares WHERE id = ?", ("expired1",))
        assert cursor.fetchone() is None
        cursor = db_conn.execute("SELECT id FROM shares WHERE id = ?", ("valid",))
        assert cursor.fetchone() is not None


class TestHashPassword:
    """Tests for _hash_password function"""
    
    def test_hash_returns_string(self):
        """Test that hash returns a string"""
        result = _hash_password("testpassword")
        assert isinstance(result, str)
        assert len(result) > 0
    
    def test_hash_different_for_same_password(self):
        """Test that hashing same password twice gives different results"""
        hash1 = _hash_password("testpassword")
        hash2 = _hash_password("testpassword")
        # Should be different due to salt
        assert hash1 != hash2


class TestVerifyPassword:
    """Tests for _verify_password function"""
    
    def test_verify_correct_password(self):
        """Test verifying correct password"""
        password = "testpassword123"
        password_hash = _hash_password(password)
        assert _verify_password(password, password_hash) is True
    
    def test_verify_wrong_password(self):
        """Test verifying wrong password"""
        password_hash = _hash_password("correct_password")
        assert _verify_password("wrong_password", password_hash) is False


class TestCreateUser:
    """Tests for _create_user function"""
    
    def test_create_user_success(self, db_conn):
        """Test creating a new user successfully"""
        user = _create_user(db_conn, "testuser", "password123")
        
        assert user['username'] == "testuser"
        assert user['role'] == "user"
        assert user['id'] is not None
    
    def test_create_user_custom_role(self, db_conn):
        """Test creating a user with custom role"""
        user = _create_user(db_conn, "adminuser", "password123", role="admin")
        assert user['role'] == "admin"
    
    def test_create_user_duplicate_username(self, db_conn):
        """Test that duplicate username raises ValueError"""
        _create_user(db_conn, "testuser", "password123")
        
        with pytest.raises(ValueError):
            _create_user(db_conn, "testuser", "different_password")


class TestGetUserByUsername:
    """Tests for _get_user_by_username function"""
    
    def test_get_existing_user(self, db_conn):
        """Test getting an existing user"""
        _create_user(db_conn, "testuser", "password123")
        
        user = _get_user_by_username(db_conn, "testuser")
        
        assert user is not None
        assert user['username'] == "testuser"
    
    def test_get_nonexistent_user(self, db_conn):
        """Test getting a non-existent user returns None"""
        user = _get_user_by_username(db_conn, "nonexistent")
        assert user is None


class TestGetAllUsers:
    """Tests for _get_all_users function"""
    
    def test_get_all_users_empty(self, db_conn):
        """Test getting users when none exist"""
        result = _get_all_users(db_conn)
        assert result == []
    
    def test_get_all_users_multiple(self, db_conn):
        """Test getting multiple users"""
        _create_user(db_conn, "user1", "pass1")
        _create_user(db_conn, "user2", "pass2")
        
        result = _get_all_users(db_conn)
        
        assert len(result) == 2
        usernames = [u['username'] for u in result]
        assert "user1" in usernames
        assert "user2" in usernames


class TestSearchUsers:
    """Tests for _search_users function"""
    
    def test_search_users_by_partial_username(self, db_conn):
        """Test searching users by partial username"""
        _create_user(db_conn, "john_doe", "pass1")
        _create_user(db_conn, "jane_doe", "pass2")
        _create_user(db_conn, "bob_smith", "pass3")
        
        result = _search_users(db_conn, "doe")
        
        assert len(result) == 2
        usernames = [u['username'] for u in result]
        assert "john_doe" in usernames
        assert "jane_doe" in usernames


class TestUpdateUser:
    """Tests for _update_user function"""
    
    def test_update_user_role(self, db_conn):
        """Test updating user role"""
        user = _create_user(db_conn, "testuser", "pass1")
        
        result = _update_user(db_conn, user['id'], role="admin")
        
        assert result is True
        updated = _get_user_by_username(db_conn, "testuser")
        assert updated['role'] == "admin"
    
    def test_update_user_no_valid_fields(self, db_conn):
        """Test updating with no valid fields returns False"""
        user = _create_user(db_conn, "testuser", "pass1")
        
        result = _update_user(db_conn, user['id'], invalid_field="value")
        
        assert result is False
    
    def test_update_user_password(self, db_conn):
        """Test updating user password via 'password' field"""
        user = _create_user(db_conn, "testuser", "old_password")
        old_hash = _get_user_by_username(db_conn, "testuser")['password_hash']
        
        result = _update_user(db_conn, user['id'], password="new_password")
        
        assert result is True
        updated = _get_user_by_username(db_conn, "testuser")
        # Password hash should have changed
        assert updated['password_hash'] != old_hash
        # New password should verify correctly
        assert _verify_password("new_password", updated['password_hash']) is True


class TestDeleteUser:
    """Tests for _delete_user function"""
    
    def test_delete_existing_user(self, db_conn):
        """Test deleting an existing user"""
        user = _create_user(db_conn, "testuser", "pass1")
        
        result = _delete_user(db_conn, user['id'])
        
        assert result is True
        assert _get_user_by_username(db_conn, "testuser") is None
    
    def test_delete_nonexistent_user(self, db_conn):
        """Test deleting a non-existent user returns False"""
        result = _delete_user(db_conn, 99999)
        assert result is False


class TestAuthenticateUser:
    """Tests for _authenticate_user function"""
    
    def test_authenticate_valid_credentials(self, db_conn):
        """Test authenticating with valid credentials"""
        _create_user(db_conn, "testuser", "password123")
        
        result = _authenticate_user(db_conn, "testuser", "password123")
        
        assert result is not None
        assert result['username'] == "testuser"
    
    def test_authenticate_wrong_password(self, db_conn):
        """Test authenticating with wrong password"""
        _create_user(db_conn, "testuser", "password123")
        
        result = _authenticate_user(db_conn, "testuser", "wrongpassword")
        
        assert result is None
    
    def test_authenticate_nonexistent_user(self, db_conn):
        """Test authenticating non-existent user"""
        result = _authenticate_user(db_conn, "nonexistent", "password123")
        assert result is None


class TestAssignAdminPrivileges:
    """Tests for _assign_admin_privileges function"""
    
    def test_assign_admin_to_existing_user(self, db_conn):
        """Test assigning admin privileges to existing user"""
        _create_user(db_conn, "testuser", "password123")
        
        _assign_admin_privileges(db_conn, ["testuser"])
        
        user = _get_user_by_username(db_conn, "testuser")
        assert user['role'] == "admin"
    
    def test_assign_admin_empty_list(self, db_conn):
        """Test with empty list does nothing"""
        _create_user(db_conn, "testuser", "password123")
        
        _assign_admin_privileges(db_conn, [])
        
        user = _get_user_by_username(db_conn, "testuser")
        assert user['role'] == "user"


class TestExtractUsernameFromDn:
    """Tests for _extract_username_from_dn function"""
    
    def test_extract_username_simple_dn(self):
        """Test extracting username from simple DN"""
        dn = "uid=john.doe,ou=users,dc=example,dc=com"
        template = "uid={username},ou=users,dc=example,dc=com"
        
        result = _extract_username_from_dn(dn, template)
        assert result == "john.doe"
    
    def test_extract_username_complex_dn(self):
        """Test extracting username from complex DN with CN"""
        dn = "CN=John Doe,OU=Users,DC=example,DC=com"
        template = "CN={username},OU=Users,DC=example,DC=com"
        
        result = _extract_username_from_dn(dn, template)
        # Function looks for 'cn' (lowercase) in DN parts
        # It will find 'CN=John Doe' and extract 'John Doe'
        assert result == "John Doe"
    
    def test_extract_username_samaccountname(self):
        """Test extracting username with sAMAccountName"""
        dn = "sAMAccountName=john,CN=Users,DC=example,DC=com"
        template = "sAMAccountName={username},CN=Users,DC=example,DC=com"
        
        result = _extract_username_from_dn(dn, template)
        assert result == "john"
    
    def test_extract_username_no_match(self):
        """Test extracting username when DN doesn't have common attributes"""
        dn = "ou=users,dc=example,dc=com"
        template = "ou={username},dc=example,dc=com"
        
        result = _extract_username_from_dn(dn, template)
        # Should return None if no uid/cn/sAMAccountName found
        assert result is None
