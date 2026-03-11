"""Tests for aird/db.py"""

import pytest
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from aird.db import (
    init_db,
    load_feature_flags,
    save_feature_flags,
    insert_share,
    delete_share,
    update_share,
    is_share_expired,
    cleanup_expired_shares,
    get_share_by_id,
    get_all_shares,
    get_shares_for_path,
    load_upload_config,
    save_upload_config,
    load_websocket_config,
    save_websocket_config,
    hash_password,
    verify_password,
    create_user,
    get_user_by_username,
    get_all_users,
    search_users,
    update_user,
    delete_user,
    authenticate_user,
    create_ldap_config,
    get_all_ldap_configs,
    get_ldap_config_by_id,
    update_ldap_config,
    delete_ldap_config,
    log_ldap_sync,
    get_ldap_sync_logs,
    extract_username_from_dn,
    assign_admin_privileges,
    load_allowed_extensions,
    save_allowed_extensions,
    create_network_share,
    get_all_network_shares,
    get_network_share,
    update_network_share,
    delete_network_share,
    log_audit,
    get_audit_logs,
)


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database for testing"""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


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

    def test_shares_table_has_all_columns(self):
        """Test that shares table has all required columns"""
        conn = sqlite3.connect(":memory:")
        init_db(conn)

        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]

        assert "id" in columns
        assert "created" in columns
        assert "paths" in columns
        assert "allowed_users" in columns
        assert "secret_token" in columns
        assert "share_type" in columns
        assert "allow_list" in columns
        assert "avoid_list" in columns
        assert "expiry_date" in columns
        conn.close()


class TestFeatureFlags:
    """Tests for feature flag functions"""

    def test_load_empty_feature_flags(self, db_conn):
        """Test loading feature flags from empty table"""
        result = load_feature_flags(db_conn)
        assert result == {}

    def test_save_and_load_feature_flags(self, db_conn):
        """Test saving and loading feature flags"""
        flags = {"feature1": True, "feature2": False}
        save_feature_flags(db_conn, flags)

        result = load_feature_flags(db_conn)
        assert result == flags

    def test_save_feature_flags_updates_existing(self, db_conn):
        """Test that saving updates existing flags"""
        save_feature_flags(db_conn, {"flag1": True})
        save_feature_flags(db_conn, {"flag1": False})

        result = load_feature_flags(db_conn)
        assert result["flag1"] is False


class TestShares:
    """Tests for share functions"""

    def test_insert_share_basic(self, db_conn):
        """Test inserting a basic share"""
        result = insert_share(
            db_conn,
            sid="share123",
            created="2024-01-01T00:00:00",
            paths=["/path/to/file.txt"],
        )

        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share is not None
        assert share["paths"] == ["/path/to/file.txt"]

    def test_insert_share_with_all_options(self, db_conn):
        """Test inserting a share with all options"""
        result = insert_share(
            db_conn,
            sid="share456",
            created="2024-01-01T00:00:00",
            paths=["/path/file1.txt"],
            allowed_users=["user1", "user2"],
            secret_token="token123",
            share_type="dynamic",
            allow_list=["*.txt"],
            avoid_list=["*.log"],
            expiry_date="2024-12-31T23:59:59",
        )

        assert result is True
        share = get_share_by_id(db_conn, "share456")
        assert share["allowed_users"] == ["user1", "user2"]
        assert share["secret_token"] == "token123"
        assert share["share_type"] == "dynamic"

    def test_delete_share(self, db_conn):
        """Test deleting a share"""
        insert_share(db_conn, "share123", "2024-01-01", ["/path"])
        delete_share(db_conn, "share123")

        share = get_share_by_id(db_conn, "share123")
        assert share is None

    def test_update_share_type(self, db_conn):
        """Test updating share type"""
        insert_share(db_conn, "share123", "2024-01-01", ["/path"])
        result = update_share(db_conn, "share123", share_type="dynamic")

        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share["share_type"] == "dynamic"

    def test_update_share_disable_token(self, db_conn):
        """Test disabling share token"""
        insert_share(
            db_conn, "share123", "2024-01-01", ["/path"], secret_token="oldtoken"
        )
        result = update_share(db_conn, "share123", disable_token=True)

        assert result is True
        share = get_share_by_id(db_conn, "share123")
        assert share["secret_token"] is None

    def test_update_share_no_changes(self, db_conn):
        """Test update with no changes returns False"""
        insert_share(db_conn, "share123", "2024-01-01", ["/path"])
        result = update_share(db_conn, "share123")

        assert result is False

    def test_get_all_shares(self, db_conn):
        """Test getting all shares"""
        insert_share(db_conn, "share1", "2024-01-01", ["/path1"])
        insert_share(db_conn, "share2", "2024-01-02", ["/path2"])

        shares = get_all_shares(db_conn)
        assert len(shares) == 2
        assert "share1" in shares
        assert "share2" in shares

    def test_get_shares_for_path(self, db_conn):
        """Test getting shares containing a specific path"""
        insert_share(db_conn, "share1", "2024-01-01", ["/path/file.txt"])
        insert_share(db_conn, "share2", "2024-01-02", ["/path/file.txt", "/other.txt"])
        insert_share(db_conn, "share3", "2024-01-03", ["/different.txt"])

        shares = get_shares_for_path(db_conn, "/path/file.txt")
        assert len(shares) == 2


class TestShareExpiry:
    """Tests for share expiry functions"""

    def test_is_share_expired_no_date(self):
        """Test that None expiry date is not expired"""
        assert is_share_expired(None) is False
        assert is_share_expired("") is False

    def test_is_share_expired_future_date(self):
        """Test that future date is not expired"""
        future = (datetime.now() + timedelta(days=1)).isoformat()
        assert is_share_expired(future) is False

    def test_is_share_expired_past_date(self):
        """Test that past date is expired"""
        past = (datetime.now() - timedelta(days=1)).isoformat()
        assert is_share_expired(past) is True

    def test_is_share_expired_with_z_suffix(self):
        """Test expiry date with Z suffix"""
        past = (datetime.now() - timedelta(days=1)).isoformat() + "Z"
        assert is_share_expired(past) is True

    def test_cleanup_expired_shares(self, db_conn):
        """Test cleaning up expired shares"""
        past = (datetime.now() - timedelta(days=1)).isoformat()
        future = (datetime.now() + timedelta(days=1)).isoformat()

        insert_share(db_conn, "expired", "2024-01-01", ["/path1"], expiry_date=past)
        insert_share(db_conn, "valid", "2024-01-01", ["/path2"], expiry_date=future)
        insert_share(db_conn, "no_expiry", "2024-01-01", ["/path3"])

        deleted = cleanup_expired_shares(db_conn)

        assert deleted == 1
        assert get_share_by_id(db_conn, "expired") is None
        assert get_share_by_id(db_conn, "valid") is not None
        assert get_share_by_id(db_conn, "no_expiry") is not None


class TestWebsocketConfig:
    """Tests for websocket config functions"""

    def test_load_empty_config(self, db_conn):
        """Test loading empty config"""
        result = load_websocket_config(db_conn)
        assert result == {}

    def test_save_and_load_config(self, db_conn):
        """Test saving and loading config"""
        config = {"max_connections": 100, "timeout": 30}
        save_websocket_config(db_conn, config)

        result = load_websocket_config(db_conn)
        assert result == config


class TestUploadConfig:
    """Tests for upload configuration functions"""

    def test_load_empty_config(self, db_conn):
        """Test loading upload config from empty table"""
        result = load_upload_config(db_conn)
        assert result == {}

    def test_save_and_load_config(self, db_conn):
        """Test saving and loading upload config"""
        config = {"max_file_size_mb": 1024}
        save_upload_config(db_conn, config)

        result = load_upload_config(db_conn)
        assert result == config

    def test_save_upload_config_updates_existing(self, db_conn):
        """Test that saving updates existing config values"""
        save_upload_config(db_conn, {"max_file_size_mb": 512})
        save_upload_config(db_conn, {"max_file_size_mb": 1024})

        result = load_upload_config(db_conn)
        assert result["max_file_size_mb"] == 1024

    def test_save_multiple_keys(self, db_conn):
        """Test saving multiple config keys"""
        config = {"max_file_size_mb": 256, "other_key": 100}
        save_upload_config(db_conn, config)

        result = load_upload_config(db_conn)
        assert result["max_file_size_mb"] == 256
        assert result["other_key"] == 100

    def test_load_upload_config_creates_table(self):
        """Test that load_upload_config creates the table if it doesn't exist"""
        conn = sqlite3.connect(":memory:")
        result = load_upload_config(conn)
        assert result == {}
        conn.close()

    def test_save_upload_config_creates_table(self):
        """Test that save_upload_config creates the table if it doesn't exist"""
        conn = sqlite3.connect(":memory:")
        save_upload_config(conn, {"max_file_size_mb": 512})
        result = load_upload_config(conn)
        assert result["max_file_size_mb"] == 512
        conn.close()


class TestPasswordHashing:
    """Tests for password hashing functions"""

    def test_hash_password_produces_hash(self):
        """Test that hash_password produces a hash"""
        result = hash_password("testpassword")
        assert result is not None
        assert len(result) > 0
        assert result != "testpassword"

    def test_verify_password_correct(self):
        """Test verifying correct password"""
        password = "testpassword123"
        hash_val = hash_password(password)

        assert verify_password(password, hash_val) is True

    def test_verify_password_incorrect(self):
        """Test verifying incorrect password"""
        hash_val = hash_password("correct")

        assert verify_password("wrong", hash_val) is False

    def test_verify_password_legacy_format(self):
        """Test verifying legacy salt:hash format"""
        import hashlib
        import secrets

        salt = secrets.token_hex(32)
        password = "testpassword"
        pwd_hash = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        legacy_hash = f"{salt}:{pwd_hash}"

        assert verify_password(password, legacy_hash) is True
        assert verify_password("wrong", legacy_hash) is False


class TestUserManagement:
    """Tests for user management functions"""

    def test_create_user(self, db_conn):
        """Test creating a user"""
        user = create_user(db_conn, "testuser", "password123")

        assert user["username"] == "testuser"
        assert user["role"] == "user"
        assert user["active"] is True

    def test_create_user_with_role(self, db_conn):
        """Test creating a user with specific role"""
        user = create_user(db_conn, "adminuser", "password123", role="admin")

        assert user["role"] == "admin"

    def test_create_duplicate_user_raises_error(self, db_conn):
        """Test creating duplicate user raises error"""
        create_user(db_conn, "testuser", "password123")

        with pytest.raises(ValueError, match="already exists"):
            create_user(db_conn, "testuser", "password456")

    def test_get_user_by_username(self, db_conn):
        """Test getting user by username"""
        create_user(db_conn, "testuser", "password123")

        user = get_user_by_username(db_conn, "testuser")
        assert user is not None
        assert user["username"] == "testuser"

    def test_get_nonexistent_user(self, db_conn):
        """Test getting non-existent user returns None"""
        user = get_user_by_username(db_conn, "nonexistent")
        assert user is None

    def test_get_all_users(self, db_conn):
        """Test getting all users"""
        create_user(db_conn, "user1", "pass1")
        create_user(db_conn, "user2", "pass2")

        users = get_all_users(db_conn)
        assert len(users) == 2

    def test_search_users(self, db_conn):
        """Test searching users"""
        create_user(db_conn, "john_doe", "pass1")
        create_user(db_conn, "jane_doe", "pass2")
        create_user(db_conn, "bob_smith", "pass3")

        results = search_users(db_conn, "doe")
        assert len(results) == 2

    def test_update_user_role(self, db_conn):
        """Test updating user role"""
        user = create_user(db_conn, "testuser", "password123")

        result = update_user(db_conn, user["id"], role="admin")
        assert result is True

        updated = get_user_by_username(db_conn, "testuser")
        assert updated["role"] == "admin"

    def test_update_user_password(self, db_conn):
        """Test updating user password via 'password' field"""
        user = create_user(db_conn, "testuser", "old_password")
        old_hash = get_user_by_username(db_conn, "testuser")["password_hash"]

        result = update_user(db_conn, user["id"], password="new_password")
        assert result is True

        updated = get_user_by_username(db_conn, "testuser")
        # Password hash should have changed
        assert updated["password_hash"] != old_hash
        # New password should verify correctly
        assert verify_password("new_password", updated["password_hash"]) is True

    def test_delete_user(self, db_conn):
        """Test deleting user"""
        user = create_user(db_conn, "testuser", "password123")

        result = delete_user(db_conn, user["id"])
        assert result is True

        deleted = get_user_by_username(db_conn, "testuser")
        assert deleted is None

    def test_authenticate_user_success(self, db_conn):
        """Test successful authentication"""
        create_user(db_conn, "testuser", "password123")

        user = authenticate_user(db_conn, "testuser", "password123")
        assert user is not None
        assert user["username"] == "testuser"
        assert "password_hash" not in user

    def test_authenticate_user_wrong_password(self, db_conn):
        """Test authentication with wrong password"""
        create_user(db_conn, "testuser", "password123")

        user = authenticate_user(db_conn, "testuser", "wrongpassword")
        assert user is None


class TestLdapConfig:
    """Tests for LDAP configuration functions"""

    def test_create_ldap_config(self, db_conn):
        """Test creating LDAP config"""
        config = create_ldap_config(
            db_conn,
            name="Test LDAP",
            server="ldap://example.com",
            ldap_base_dn="dc=example,dc=com",
            ldap_member_attributes="member",
            user_template="uid={username},ou=users,dc=example,dc=com",
        )

        assert config["name"] == "Test LDAP"
        assert config["server"] == "ldap://example.com"
        assert config["active"] is True

    def test_create_duplicate_ldap_config_raises_error(self, db_conn):
        """Test creating duplicate LDAP config"""
        create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )

        with pytest.raises(ValueError, match="already exists"):
            create_ldap_config(
                db_conn, "Test", "ldap://b", "dc=b", "member", "uid={username}"
            )

    def test_get_all_ldap_configs(self, db_conn):
        """Test getting all LDAP configs"""
        create_ldap_config(
            db_conn, "Config1", "ldap://a", "dc=a", "member", "uid={username}"
        )
        create_ldap_config(
            db_conn, "Config2", "ldap://b", "dc=b", "member", "uid={username}"
        )

        configs = get_all_ldap_configs(db_conn)
        assert len(configs) == 2

    def test_get_ldap_config_by_id(self, db_conn):
        """Test getting LDAP config by ID"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )

        result = get_ldap_config_by_id(db_conn, config["id"])
        assert result is not None
        assert result["name"] == "Test"

    def test_get_ldap_config_by_id_not_found(self, db_conn):
        """Test getting LDAP config by non-existent ID returns None"""
        result = get_ldap_config_by_id(db_conn, 99999)
        assert result is None

    def test_update_ldap_config(self, db_conn):
        """Test updating LDAP config"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )

        result = update_ldap_config(db_conn, config["id"], server="ldap://new")
        assert result is True

        updated = get_ldap_config_by_id(db_conn, config["id"])
        assert updated["server"] == "ldap://new"

    def test_delete_ldap_config(self, db_conn):
        """Test deleting LDAP config"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )

        result = delete_ldap_config(db_conn, config["id"])
        assert result is True

        deleted = get_ldap_config_by_id(db_conn, config["id"])
        assert deleted is None


class TestLdapSyncLog:
    """Tests for LDAP sync log functions"""

    def test_log_ldap_sync(self, db_conn):
        """Test logging LDAP sync"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )

        # Should not raise
        log_ldap_sync(db_conn, config["id"], "manual", 10, 5, 2, "success")

    def test_get_ldap_sync_logs(self, db_conn):
        """Test getting LDAP sync logs"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        log_ldap_sync(db_conn, config["id"], "manual", 10, 5, 2, "success")
        log_ldap_sync(db_conn, config["id"], "scheduled", 8, 3, 1, "success")

        logs = get_ldap_sync_logs(db_conn)
        assert len(logs) == 2


class TestExtractUsernameFromDn:
    """Tests for extract_username_from_dn function"""

    def test_extract_uid(self):
        """Test extracting username from uid"""
        dn = "uid=john.doe,ou=users,dc=example,dc=com"
        template = "uid={username},ou=users,dc=example,dc=com"

        result = extract_username_from_dn(dn, template)
        assert result == "john.doe"

    def test_extract_cn(self):
        """Test extracting username from cn"""
        dn = "cn=John Doe,ou=users,dc=example,dc=com"
        template = "cn={username},ou=users,dc=example,dc=com"

        result = extract_username_from_dn(dn, template)
        assert result == "John Doe"

    def test_extract_samaccountname(self):
        """Test extracting username from sAMAccountName"""
        dn = "sAMAccountName=johndoe,cn=users,dc=example,dc=com"
        template = "sAMAccountName={username},cn=users,dc=example,dc=com"

        result = extract_username_from_dn(dn, template)
        assert result == "johndoe"

    def test_extract_no_match(self):
        """Test extraction when no match found"""
        dn = "ou=users,dc=example,dc=com"
        template = "uid={username},ou=users,dc=example,dc=com"

        result = extract_username_from_dn(dn, template)
        assert result is None


class TestAssignAdminPrivileges:
    """Tests for assign_admin_privileges function"""

    def test_assign_to_existing_user(self, db_conn):
        """Test assigning admin to existing user"""
        create_user(db_conn, "testuser", "password123", role="user")

        assign_admin_privileges(db_conn, ["testuser"])

        user = get_user_by_username(db_conn, "testuser")
        assert user["role"] == "admin"

    def test_assign_to_nonexistent_user(self, db_conn):
        """Test assigning admin to non-existent user doesn't raise"""
        # Should not raise
        assign_admin_privileges(db_conn, ["nonexistent"])

    def test_assign_empty_list(self, db_conn):
        """Test with empty list"""
        # Should not raise
        assign_admin_privileges(db_conn, [])

    def test_assign_none(self, db_conn):
        """Test with None"""
        # Should not raise
        assign_admin_privileges(db_conn, None)

    def test_assign_skips_non_string(self, db_conn):
        assign_admin_privileges(db_conn, [123, None, ""])

    def test_assign_already_admin(self, db_conn):
        create_user(db_conn, "adminuser", "pass", role="admin")
        assign_admin_privileges(db_conn, ["adminuser"])
        user = get_user_by_username(db_conn, "adminuser")
        assert user["role"] == "admin"


class TestAllowedExtensions:
    def test_load_empty(self, db_conn):
        result = load_allowed_extensions(db_conn)
        assert result == set()

    def test_save_and_load(self, db_conn):
        save_allowed_extensions(db_conn, {".txt", ".pdf", ".jpg"})
        result = load_allowed_extensions(db_conn)
        assert result == {".txt", ".pdf", ".jpg"}

    def test_save_replaces(self, db_conn):
        save_allowed_extensions(db_conn, {".txt", ".pdf"})
        save_allowed_extensions(db_conn, {".jpg"})
        result = load_allowed_extensions(db_conn)
        assert result == {".jpg"}

    def test_save_filters_invalid(self, db_conn):
        save_allowed_extensions(db_conn, {"txt", 123, "", None, ".pdf"})
        result = load_allowed_extensions(db_conn)
        assert result == {".pdf"}

    def test_load_with_closed_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.close()
        result = load_allowed_extensions(conn)
        assert result == set()


class TestNetworkSharesCRUD:
    def test_create_and_get(self, db_conn):
        ok = create_network_share(
            db_conn, "ns1", "Share1", "/data", "webdav", 8080, "user", "pass"
        )
        assert ok is True
        share = get_network_share(db_conn, "ns1")
        assert share is not None
        assert share["name"] == "Share1"
        assert share["port"] == 8080
        assert share["read_only"] is False

    def test_create_read_only(self, db_conn):
        create_network_share(
            db_conn, "ns2", "RO", "/data", "smb", 445, "u", "p", read_only=True
        )
        share = get_network_share(db_conn, "ns2")
        assert share["read_only"] is True

    def test_get_all(self, db_conn):
        create_network_share(db_conn, "a", "A", "/a", "webdav", 80, "u", "p")
        create_network_share(db_conn, "b", "B", "/b", "smb", 445, "u", "p")
        shares = get_all_network_shares(db_conn)
        assert len(shares) == 2

    def test_get_nonexistent(self, db_conn):
        assert get_network_share(db_conn, "nope") is None

    def test_update(self, db_conn):
        create_network_share(db_conn, "ns1", "Old", "/data", "webdav", 80, "u", "p")
        ok = update_network_share(db_conn, "ns1", name="New", port=9090)
        assert ok is True
        share = get_network_share(db_conn, "ns1")
        assert share["name"] == "New"
        assert share["port"] == 9090

    def test_update_no_valid_fields(self, db_conn):
        create_network_share(db_conn, "ns1", "X", "/x", "webdav", 80, "u", "p")
        assert update_network_share(db_conn, "ns1", bad_field="value") is False

    def test_update_boolean_fields(self, db_conn):
        create_network_share(db_conn, "ns1", "X", "/x", "webdav", 80, "u", "p")
        update_network_share(db_conn, "ns1", enabled=False, read_only=True)
        share = get_network_share(db_conn, "ns1")
        assert share["enabled"] is False
        assert share["read_only"] is True

    def test_delete(self, db_conn):
        create_network_share(db_conn, "ns1", "X", "/x", "webdav", 80, "u", "p")
        assert delete_network_share(db_conn, "ns1") is True
        assert get_network_share(db_conn, "ns1") is None

    def test_delete_nonexistent(self, db_conn):
        assert delete_network_share(db_conn, "nope") is False

    def test_create_duplicate(self, db_conn):
        create_network_share(db_conn, "ns1", "X", "/x", "webdav", 80, "u", "p")
        ok = create_network_share(db_conn, "ns1", "Y", "/y", "smb", 445, "u2", "p2")
        assert ok is False


class TestAuditLog:
    def test_log_and_get(self, db_conn):
        log_audit(
            db_conn, "file_upload", username="admin", details="test.txt", ip="127.0.0.1"
        )
        logs = get_audit_logs(db_conn)
        assert len(logs) == 1
        assert logs[0]["action"] == "file_upload"
        assert logs[0]["username"] == "admin"

    def test_log_none_conn(self):
        log_audit(None, "test")

    def test_get_none_conn(self):
        assert get_audit_logs(None) == []

    def test_get_with_limit(self, db_conn):
        for i in range(10):
            log_audit(db_conn, f"action_{i}")
        logs = get_audit_logs(db_conn, limit=3)
        assert len(logs) == 3

    def test_get_with_offset(self, db_conn):
        for i in range(5):
            log_audit(db_conn, f"action_{i}")
        logs = get_audit_logs(db_conn, limit=2, offset=3)
        assert len(logs) == 2


class TestPasswordEdgeCases:
    def test_verify_empty_hash(self):
        assert verify_password("password", "") is False

    def test_verify_scrypt_format(self):
        import hashlib
        import secrets as s

        salt = s.token_hex(16)
        key = hashlib.scrypt(
            "mypass".encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32
        )
        scrypt_hash = f"scrypt:{salt}:{key.hex()}"
        assert verify_password("mypass", scrypt_hash) is True
        assert verify_password("wrong", scrypt_hash) is False

    def test_verify_scrypt_malformed(self):
        assert verify_password("pass", "scrypt:onlyonepart") is False

    def test_verify_legacy_malformed(self):
        assert verify_password("pass", "nocolon") is False

    def test_hash_scrypt_fallback(self):
        with patch("aird.db.ARGON2_AVAILABLE", False), patch("aird.db.PH", None):
            h = hash_password("test")
            assert h.startswith("scrypt:")
            assert verify_password("test", h) is True


class TestUpdateShareEdgeCases:
    def test_custom_secret_token(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        update_share(db_conn, "s1", secret_token="custom_token")
        share = get_share_by_id(db_conn, "s1")
        assert share["secret_token"] == "custom_token"

    def test_update_allowed_users_and_paths(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        update_share(db_conn, "s1", allowed_users=["u1"], paths=["/b", "/c"])
        share = get_share_by_id(db_conn, "s1")
        assert share["allowed_users"] == ["u1"]
        assert share["paths"] == ["/b", "/c"]

    def test_update_allow_avoid_expiry(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        update_share(
            db_conn,
            "s1",
            allow_list=["*.txt"],
            avoid_list=["*.log"],
            expiry_date="2025-12-31",
        )
        share = get_share_by_id(db_conn, "s1")
        assert share["allow_list"] == ["*.txt"]
        assert share["avoid_list"] == ["*.log"]
        assert share["expiry_date"] == "2025-12-31"


class TestAuthenticateEdgeCases:
    def test_inactive_user(self, db_conn):
        user = create_user(db_conn, "inactive", "pass123")
        update_user(db_conn, user["id"], active=False)
        result = authenticate_user(db_conn, "inactive", "pass123")
        assert result is None


class TestFeatureFlagEdgeCases:
    def test_load_on_error(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("boom")
        assert load_feature_flags(mock_conn) == {}

    def test_save_on_error(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=Exception("boom"))
        save_feature_flags(mock_conn, {"a": True})


class TestCleanupExpiredEdgeCases:
    def test_exception_returns_zero(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("boom")
        assert cleanup_expired_shares(mock_conn) == 0

    def test_is_share_expired_bad_format(self):
        assert is_share_expired("not-a-date") is False


class TestShareByIdEdgeCases:
    def test_exception_returns_none(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("boom")
        assert get_share_by_id(mock_conn, "x") is None

    def test_not_found(self, db_conn):
        assert get_share_by_id(db_conn, "nonexistent") is None

    def test_get_all_shares_exception(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("boom")
        assert get_all_shares(mock_conn) == {}

    def test_get_shares_for_path_exception(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("boom")
        assert get_shares_for_path(mock_conn, "/x") == []
