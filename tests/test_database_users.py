"""Tests for aird/database/users.py"""

import pytest
import sqlite3
from datetime import datetime
from unittest.mock import patch, MagicMock

from aird.database.users import (
    hash_password,
    verify_password,
    create_user,
    get_user_by_username,
    get_all_users,
    search_users,
    update_user,
    delete_user,
    authenticate_user,
    assign_admin_privileges,
    ARGON2_AVAILABLE
)


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database for testing"""
    conn = sqlite3.connect(":memory:")
    conn.execute('''
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_login TEXT
        )
    ''')
    conn.commit()
    yield conn
    conn.close()


class TestHashPassword:
    """Tests for hash_password function"""
    
    def test_hash_returns_string(self):
        """Test that hash returns a string"""
        result = hash_password("testpassword")
        assert isinstance(result, str)
        assert len(result) > 0
    
    def test_hash_different_for_same_password(self):
        """Test that hashing same password twice gives different results (due to salt)"""
        hash1 = hash_password("testpassword")
        hash2 = hash_password("testpassword")
        # Argon2 and legacy both use random salt, so hashes should differ
        assert hash1 != hash2
    
    def test_hash_contains_argon2_prefix_if_available(self):
        """Test hash format depends on Argon2 availability"""
        result = hash_password("testpassword")
        if ARGON2_AVAILABLE:
            assert result.startswith("$argon2")
        else:
            assert ":" in result  # Legacy format: salt:hash


class TestVerifyPassword:
    """Tests for verify_password function"""
    
    def test_verify_correct_password(self):
        """Test verifying correct password"""
        password = "testpassword123"
        password_hash = hash_password(password)
        assert verify_password(password, password_hash) is True
    
    def test_verify_wrong_password(self):
        """Test verifying wrong password"""
        password_hash = hash_password("correct_password")
        assert verify_password("wrong_password", password_hash) is False
    
    def test_verify_empty_password(self):
        """Test verifying empty password"""
        password_hash = hash_password("testpassword")
        assert verify_password("", password_hash) is False
    
    def test_verify_legacy_hash_format(self):
        """Test verifying legacy hash format (salt:hash)"""
        import hashlib
        import secrets
        
        password = "testpassword"
        salt = secrets.token_hex(32)
        pwd_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
        legacy_hash = f"{salt}:{pwd_hash}"
        
        assert verify_password(password, legacy_hash) is True
        assert verify_password("wrongpassword", legacy_hash) is False
    
    def test_verify_invalid_hash_format(self):
        """Test verifying with invalid hash format"""
        assert verify_password("test", "invalid_hash_no_colon") is False
        assert verify_password("test", "") is False


class TestCreateUser:
    """Tests for create_user function"""
    
    def test_create_user_success(self, db_conn):
        """Test creating a new user successfully"""
        user = create_user(db_conn, "testuser", "password123")
        
        assert user['username'] == "testuser"
        assert user['role'] == "user"
        assert user['active'] is True
        assert user['id'] is not None
        assert 'password' not in user  # Should not include password
        assert 'password_hash' not in user  # Should not include hash
    
    def test_create_user_custom_role(self, db_conn):
        """Test creating a user with custom role"""
        user = create_user(db_conn, "adminuser", "password123", role="admin")
        assert user['role'] == "admin"
    
    def test_create_user_duplicate_username(self, db_conn):
        """Test that duplicate username raises ValueError"""
        create_user(db_conn, "testuser", "password123")
        
        with pytest.raises(ValueError) as exc_info:
            create_user(db_conn, "testuser", "different_password")
        assert "already exists" in str(exc_info.value)
    
    def test_create_user_stores_hashed_password(self, db_conn):
        """Test that password is stored hashed, not plain text"""
        create_user(db_conn, "testuser", "password123")
        
        cursor = db_conn.execute("SELECT password_hash FROM users WHERE username = ?", ("testuser",))
        stored_hash = cursor.fetchone()[0]
        
        assert stored_hash != "password123"
        assert verify_password("password123", stored_hash) is True


class TestGetUserByUsername:
    """Tests for get_user_by_username function"""
    
    def test_get_existing_user(self, db_conn):
        """Test getting an existing user"""
        create_user(db_conn, "testuser", "password123")
        
        user = get_user_by_username(db_conn, "testuser")
        
        assert user is not None
        assert user['username'] == "testuser"
        assert 'password_hash' in user
    
    def test_get_nonexistent_user(self, db_conn):
        """Test getting a non-existent user returns None"""
        user = get_user_by_username(db_conn, "nonexistent")
        assert user is None
    
    def test_get_user_exception_returns_none(self):
        """Test that exceptions return None"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database error")
        
        result = get_user_by_username(mock_conn, "testuser")
        assert result is None


class TestGetAllUsers:
    """Tests for get_all_users function"""
    
    def test_get_all_users_empty(self, db_conn):
        """Test getting users when none exist"""
        result = get_all_users(db_conn)
        assert result == []
    
    def test_get_all_users_multiple(self, db_conn):
        """Test getting multiple users"""
        create_user(db_conn, "user1", "pass1")
        create_user(db_conn, "user2", "pass2")
        create_user(db_conn, "user3", "pass3")
        
        result = get_all_users(db_conn)
        
        assert len(result) == 3
        usernames = [u['username'] for u in result]
        assert "user1" in usernames
        assert "user2" in usernames
        assert "user3" in usernames
    
    def test_get_all_users_excludes_password_hash(self, db_conn):
        """Test that password_hash is not included in results"""
        create_user(db_conn, "testuser", "password123")
        
        result = get_all_users(db_conn)
        
        assert 'password_hash' not in result[0]
    
    def test_get_all_users_exception_returns_empty(self):
        """Test that exceptions return empty list"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database error")
        
        result = get_all_users(mock_conn)
        assert result == []


class TestSearchUsers:
    """Tests for search_users function"""
    
    def test_search_users_by_partial_username(self, db_conn):
        """Test searching users by partial username"""
        create_user(db_conn, "john_doe", "pass1")
        create_user(db_conn, "jane_doe", "pass2")
        create_user(db_conn, "bob_smith", "pass3")
        
        result = search_users(db_conn, "doe")
        
        assert len(result) == 2
        usernames = [u['username'] for u in result]
        assert "john_doe" in usernames
        assert "jane_doe" in usernames
    
    def test_search_users_case_insensitive(self, db_conn):
        """Test that search is case insensitive"""
        create_user(db_conn, "JohnDoe", "pass1")
        
        result = search_users(db_conn, "johndoe")
        assert len(result) == 1
    
    def test_search_users_only_active(self, db_conn):
        """Test that search only returns active users"""
        create_user(db_conn, "activeuser", "pass1")
        create_user(db_conn, "inactiveuser", "pass2")
        
        # Deactivate one user
        db_conn.execute("UPDATE users SET active = 0 WHERE username = 'inactiveuser'")
        db_conn.commit()
        
        result = search_users(db_conn, "user")
        
        assert len(result) == 1
        assert result[0]['username'] == "activeuser"
    
    def test_search_users_exception_returns_empty(self):
        """Test that exceptions return empty list"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database error")
        
        result = search_users(mock_conn, "test")
        assert result == []


class TestUpdateUser:
    """Tests for update_user function"""
    
    def test_update_user_role(self, db_conn):
        """Test updating user role"""
        user = create_user(db_conn, "testuser", "pass1")
        
        result = update_user(db_conn, user['id'], role="admin")
        
        assert result is True
        updated = get_user_by_username(db_conn, "testuser")
        assert updated['role'] == "admin"
    
    def test_update_user_active_status(self, db_conn):
        """Test updating user active status"""
        user = create_user(db_conn, "testuser", "pass1")
        
        result = update_user(db_conn, user['id'], active=False)
        
        assert result is True
        updated = get_user_by_username(db_conn, "testuser")
        assert updated['active'] is False
    
    def test_update_user_no_valid_fields(self, db_conn):
        """Test updating with no valid fields returns False"""
        user = create_user(db_conn, "testuser", "pass1")
        
        result = update_user(db_conn, user['id'], invalid_field="value")
        
        assert result is False
    
    def test_update_user_last_login(self, db_conn):
        """Test updating last_login"""
        user = create_user(db_conn, "testuser", "pass1")
        login_time = datetime.now().isoformat()
        
        result = update_user(db_conn, user['id'], last_login=login_time)
        
        assert result is True
        updated = get_user_by_username(db_conn, "testuser")
        assert updated['last_login'] == login_time
    
    def test_update_user_password(self, db_conn):
        """Test updating user password via 'password' field"""
        # Create user with initial password
        user = create_user(db_conn, "testuser", "old_password")
        old_hash = get_user_by_username(db_conn, "testuser")['password_hash']
        
        # Update password using the 'password' field
        result = update_user(db_conn, user['id'], password="new_password")
        
        assert result is True
        updated = get_user_by_username(db_conn, "testuser")
        # Password hash should have changed
        assert updated['password_hash'] != old_hash
        # New password should verify correctly
        assert verify_password("new_password", updated['password_hash']) is True
        # Old password should NOT verify
        assert verify_password("old_password", updated['password_hash']) is False
    
    def test_update_user_exception_returns_false(self):
        """Test that exceptions return False"""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=Exception("Database error"))
        
        result = update_user(mock_conn, 1, role="admin")
        assert result is False


class TestDeleteUser:
    """Tests for delete_user function"""
    
    def test_delete_existing_user(self, db_conn):
        """Test deleting an existing user"""
        user = create_user(db_conn, "testuser", "pass1")
        
        result = delete_user(db_conn, user['id'])
        
        assert result is True
        assert get_user_by_username(db_conn, "testuser") is None
    
    def test_delete_nonexistent_user(self, db_conn):
        """Test deleting a non-existent user returns False"""
        result = delete_user(db_conn, 99999)
        assert result is False
    
    def test_delete_user_exception_returns_false(self):
        """Test that exceptions return False"""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=Exception("Database error"))
        
        result = delete_user(mock_conn, 1)
        assert result is False


class TestAuthenticateUser:
    """Tests for authenticate_user function"""
    
    def test_authenticate_valid_credentials(self, db_conn):
        """Test authenticating with valid credentials"""
        create_user(db_conn, "testuser", "password123")
        
        result = authenticate_user(db_conn, "testuser", "password123")
        
        assert result is not None
        assert result['username'] == "testuser"
    
    def test_authenticate_wrong_password(self, db_conn):
        """Test authenticating with wrong password"""
        create_user(db_conn, "testuser", "password123")
        
        result = authenticate_user(db_conn, "testuser", "wrongpassword")
        
        assert result is None
    
    def test_authenticate_nonexistent_user(self, db_conn):
        """Test authenticating non-existent user"""
        result = authenticate_user(db_conn, "nonexistent", "password123")
        assert result is None
    
    def test_authenticate_inactive_user(self, db_conn):
        """Test authenticating inactive user returns None"""
        user = create_user(db_conn, "testuser", "password123")
        update_user(db_conn, user['id'], active=False)
        
        result = authenticate_user(db_conn, "testuser", "password123")
        
        assert result is None
    
    def test_authenticate_updates_last_login(self, db_conn):
        """Test that authentication updates last_login"""
        create_user(db_conn, "testuser", "password123")
        
        authenticate_user(db_conn, "testuser", "password123")
        
        user = get_user_by_username(db_conn, "testuser")
        assert user['last_login'] is not None


class TestAssignAdminPrivileges:
    """Tests for assign_admin_privileges function"""
    
    def test_assign_admin_to_existing_user(self, db_conn):
        """Test assigning admin privileges to existing user"""
        create_user(db_conn, "testuser", "password123")
        
        assign_admin_privileges(db_conn, ["testuser"])
        
        user = get_user_by_username(db_conn, "testuser")
        assert user['role'] == "admin"
    
    def test_assign_admin_empty_list(self, db_conn):
        """Test with empty list does nothing"""
        create_user(db_conn, "testuser", "password123")
        
        assign_admin_privileges(db_conn, [])
        
        user = get_user_by_username(db_conn, "testuser")
        assert user['role'] == "user"
    
    def test_assign_admin_nonexistent_user(self, db_conn):
        """Test assigning admin to non-existent user does nothing"""
        # Should not raise exception
        assign_admin_privileges(db_conn, ["nonexistent"])
    
    def test_assign_admin_already_admin(self, db_conn):
        """Test assigning admin to user who is already admin"""
        create_user(db_conn, "testuser", "password123", role="admin")
        
        assign_admin_privileges(db_conn, ["testuser"])
        
        user = get_user_by_username(db_conn, "testuser")
        assert user['role'] == "admin"
    
    def test_assign_admin_multiple_users(self, db_conn):
        """Test assigning admin to multiple users"""
        create_user(db_conn, "user1", "pass1")
        create_user(db_conn, "user2", "pass2")
        
        assign_admin_privileges(db_conn, ["user1", "user2"])
        
        assert get_user_by_username(db_conn, "user1")['role'] == "admin"
        assert get_user_by_username(db_conn, "user2")['role'] == "admin"
