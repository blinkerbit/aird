"""Tests for aird/main.py"""

import pytest
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from aird.database.db import get_data_dir
from aird.db import (
    init_db,
    is_share_expired,
    cleanup_expired_shares,
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
    insert_share,
    delete_share,
    update_share,
    get_share_by_id,
    get_all_shares,
    get_shares_for_path,
    load_upload_config,
    save_upload_config,
)
from aird.database.ldap import (
    create_ldap_config,
    delete_ldap_config,
    extract_username_from_dn,
    get_all_ldap_configs,
    get_ldap_config_by_id,
    get_ldap_sync_logs,
    log_ldap_sync,
    update_ldap_config,
)
from aird.main import make_app, print_banner


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database for testing"""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


class TestGetDataDir:
    """Tests for get_data_dir function"""

    def testget_data_dir_windows(self):
        """Test data directory on Windows"""
        with patch("os.name", "nt"), patch(
            "os.environ.get",
            side_effect=lambda k, d=None: (
                "C:\\Users\\Test\\AppData\\Local" if k == "LOCALAPPDATA" else d
            ),
        ), patch(
            "os.path.expanduser", return_value="C:\\Users\\Test\\AppData\\Local"
        ), patch(
            "os.makedirs"
        ), patch(
            "os.path.join", side_effect=lambda *args: "\\".join(args)
        ):
            result = get_data_dir()
            assert "aird" in result

    def testget_data_dir_macos(self):
        """Test data directory on macOS"""
        with patch("os.name", "posix"), patch("sys.platform", "darwin"), patch(
            "os.path.expanduser", return_value="/Users/test/Library/Application Support"
        ), patch("os.makedirs"), patch(
            "os.path.join", side_effect=lambda *args: "/".join(args)
        ):
            result = get_data_dir()
            assert "aird" in result

    def testget_data_dir_linux(self):
        """Test data directory on Linux"""
        with patch("os.name", "posix"), patch("sys.platform", "linux"), patch(
            "os.environ.get",
            side_effect=lambda k, d=None: (
                "/home/test/.local/share" if k == "XDG_DATA_HOME" else d
            ),
        ), patch("os.path.expanduser", return_value="/home/test/.local/share"), patch(
            "os.makedirs"
        ), patch(
            "os.path.join", side_effect=lambda *args: "/".join(args)
        ):
            result = get_data_dir()
            assert "aird" in result

    def testget_data_dir_fallback(self):
        """Test data directory fallback on exception"""
        with patch("os.name", "nt"), patch(
            "os.environ.get", side_effect=Exception("Error")
        ), patch("os.getcwd", return_value="/current/dir"):
            result = get_data_dir()
            assert result == "/current/dir"


class TestInitDb:
    """Tests for init_db function"""

    def testinit_db_creates_tables(self, db_conn):
        """Test that init_db creates all required tables"""
        cursor = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        assert "feature_flags" in tables
        assert "shares" in tables
        assert "users" in tables
        assert "ldap_configs" in tables
        assert "ldap_sync_log" in tables

    def testinit_db_adds_missing_columns(self):
        """Test that init_db adds missing columns to shares table"""
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

        init_db(conn)

        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]

        assert "allowed_users" in columns
        assert "secret_token" in columns
        assert "share_type" in columns
        assert "allow_list" in columns
        assert "avoid_list" in columns
        assert "expiry_date" in columns

        conn.close()


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
        """Test that invalid date format returns False"""
        assert is_share_expired("invalid-date") is False


class TestCleanupExpiredShares:
    """Tests for cleanup_expired_shares function"""

    def test_cleanup_removes_expired_shares(self, db_conn):
        """Test that cleanup removes expired shares"""
        past_date = (datetime.now() - timedelta(days=1)).isoformat()
        future_date = (datetime.now() + timedelta(days=1)).isoformat()

        db_conn.execute(
            "INSERT INTO shares (id, created, paths, expiry_date) VALUES (?, ?, ?, ?)",
            ("expired1", "2024-01-01", '["/path1"]', past_date),
        )
        db_conn.execute(
            "INSERT INTO shares (id, created, paths, expiry_date) VALUES (?, ?, ?, ?)",
            ("expired2", "2024-01-01", '["/path2"]', past_date),
        )
        db_conn.execute(
            "INSERT INTO shares (id, created, paths, expiry_date) VALUES (?, ?, ?, ?)",
            ("valid", "2024-01-01", '["/path3"]', future_date),
        )
        db_conn.commit()

        deleted_count = cleanup_expired_shares(db_conn)

        assert deleted_count == 2
        cursor = db_conn.execute("SELECT id FROM shares WHERE id = ?", ("expired1",))
        assert cursor.fetchone() is None
        cursor = db_conn.execute("SELECT id FROM shares WHERE id = ?", ("valid",))
        assert cursor.fetchone() is not None


class TestHashPassword:
    """Tests for hash_password function"""

    def test_hash_returns_string(self):
        """Test that hash returns a string"""
        result = hash_password("testpassword")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_hash_different_for_same_password(self):
        """Test that hashing same password twice gives different results"""
        hash1 = hash_password("testpassword")
        hash2 = hash_password("testpassword")
        # Should be different due to salt
        assert hash1 != hash2


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


class TestCreateUser:
    """Tests for create_user function"""

    def testcreate_user_success(self, db_conn):
        """Test creating a new user successfully"""
        user = create_user(db_conn, "testuser", "password123")

        assert user["username"] == "testuser"
        assert user["role"] == "user"
        assert user["id"] is not None

    def testcreate_user_custom_role(self, db_conn):
        """Test creating a user with custom role"""
        user = create_user(db_conn, "adminuser", "password123", role="admin")
        assert user["role"] == "admin"

    def testcreate_user_duplicate_username(self, db_conn):
        """Test that duplicate username raises ValueError"""
        create_user(db_conn, "testuser", "password123")

        with pytest.raises(ValueError):
            create_user(db_conn, "testuser", "different_password")


class TestGetUserByUsername:
    """Tests for get_user_by_username function"""

    def test_get_existing_user(self, db_conn):
        """Test getting an existing user"""
        create_user(db_conn, "testuser", "password123")

        user = get_user_by_username(db_conn, "testuser")

        assert user is not None
        assert user["username"] == "testuser"

    def test_get_nonexistent_user(self, db_conn):
        """Test getting a non-existent user returns None"""
        user = get_user_by_username(db_conn, "nonexistent")
        assert user is None


class TestGetAllUsers:
    """Tests for get_all_users function"""

    def testget_all_users_empty(self, db_conn):
        """Test getting users when none exist"""
        result = get_all_users(db_conn)
        assert result == []

    def testget_all_users_multiple(self, db_conn):
        """Test getting multiple users"""
        create_user(db_conn, "user1", "pass1")
        create_user(db_conn, "user2", "pass2")

        result = get_all_users(db_conn)

        assert len(result) == 2
        usernames = [u["username"] for u in result]
        assert "user1" in usernames
        assert "user2" in usernames


class TestSearchUsers:
    """Tests for search_users function"""

    def testsearch_users_by_partial_username(self, db_conn):
        """Test searching users by partial username"""
        create_user(db_conn, "john_doe", "pass1")
        create_user(db_conn, "jane_doe", "pass2")
        create_user(db_conn, "bob_smith", "pass3")

        result = search_users(db_conn, "doe")

        assert len(result) == 2
        usernames = [u["username"] for u in result]
        assert "john_doe" in usernames
        assert "jane_doe" in usernames


class TestUpdateUser:
    """Tests for update_user function"""

    def testupdate_user_role(self, db_conn):
        """Test updating user role"""
        user = create_user(db_conn, "testuser", "pass1")

        result = update_user(db_conn, user["id"], role="admin")

        assert result is True
        updated = get_user_by_username(db_conn, "testuser")
        assert updated["role"] == "admin"

    def testupdate_user_no_valid_fields(self, db_conn):
        """Test updating with no valid fields returns False"""
        user = create_user(db_conn, "testuser", "pass1")

        result = update_user(db_conn, user["id"], invalid_field="value")

        assert result is False

    def testupdate_user_password(self, db_conn):
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


class TestDeleteUser:
    """Tests for delete_user function"""

    def test_delete_existing_user(self, db_conn):
        """Test deleting an existing user"""
        user = create_user(db_conn, "testuser", "pass1")

        result = delete_user(db_conn, user["id"])

        assert result is True
        assert get_user_by_username(db_conn, "testuser") is None

    def test_delete_nonexistent_user(self, db_conn):
        """Test deleting a non-existent user returns False"""
        result = delete_user(db_conn, 99999)
        assert result is False


class TestAuthenticateUser:
    """Tests for authenticate_user function"""

    def test_authenticate_valid_credentials(self, db_conn):
        """Test authenticating with valid credentials"""
        create_user(db_conn, "testuser", "password123")

        result = authenticate_user(db_conn, "testuser", "password123")

        assert result is not None
        assert result["username"] == "testuser"

    def test_authenticate_wrong_password(self, db_conn):
        """Test authenticating with wrong password"""
        create_user(db_conn, "testuser", "password123")

        result = authenticate_user(db_conn, "testuser", "wrongpassword")

        assert result is None

    def test_authenticate_nonexistent_user(self, db_conn):
        """Test authenticating non-existent user"""
        result = authenticate_user(db_conn, "nonexistent", "password123")
        assert result is None


class TestAssignAdminPrivileges:
    """Tests for assign_admin_privileges function"""

    def test_assign_admin_to_existing_user(self, db_conn):
        """Test assigning admin privileges to existing user"""
        create_user(db_conn, "testuser", "password123")

        assign_admin_privileges(db_conn, ["testuser"])

        user = get_user_by_username(db_conn, "testuser")
        assert user["role"] == "admin"

    def test_assign_admin_empty_list(self, db_conn):
        """Test with empty list does nothing"""
        create_user(db_conn, "testuser", "password123")

        assign_admin_privileges(db_conn, [])

        user = get_user_by_username(db_conn, "testuser")
        assert user["role"] == "user"


class TestExtractUsernameFromDn:
    """Tests for extract_username_from_dn function"""

    def test_extract_username_simple_dn(self):
        """Test extracting username from simple DN"""
        dn = "uid=john.doe,ou=users,dc=example,dc=com"
        template = "uid={username},ou=users,dc=example,dc=com"

        result = extract_username_from_dn(dn, template)
        assert result == "john.doe"

    def test_extract_username_complex_dn(self):
        """Test extracting username from complex DN with CN"""
        dn = "CN=John Doe,OU=Users,DC=example,DC=com"
        template = "CN={username},OU=Users,DC=example,DC=com"

        result = extract_username_from_dn(dn, template)
        # Function looks for 'cn' (lowercase) in DN parts
        # It will find 'CN=John Doe' and extract 'John Doe'
        assert result == "John Doe"

    def test_extract_username_samaccountname(self):
        """Test extracting username with sAMAccountName"""
        dn = "sAMAccountName=john,CN=Users,DC=example,DC=com"
        template = "sAMAccountName={username},CN=Users,DC=example,DC=com"

        result = extract_username_from_dn(dn, template)
        assert result == "john"

    def test_extract_username_no_match(self):
        """Test extracting username when DN doesn't have common attributes"""
        dn = "ou=users,dc=example,dc=com"
        template = "ou={username},dc=example,dc=com"

        result = extract_username_from_dn(dn, template)
        # Should return None if no uid/cn/sAMAccountName found
        assert result is None

    def test_extract_no_username_in_template(self):
        result = extract_username_from_dn("uid=john,dc=com", "static_template")
        assert result is None


class TestInsertShare:
    def test_insert_basic(self, db_conn):
        ok = insert_share(db_conn, "s1", "2024-01-01", ["/a.txt"])
        assert ok is True
        share = get_share_by_id(db_conn, "s1")
        assert share is not None
        assert share["paths"] == ["/a.txt"]

    def test_insert_with_all_options(self, db_conn):
        ok = insert_share(
            db_conn,
            "s2",
            "2024-01-01",
            ["/b.txt"],
            allowed_users=["u1"],
            secret_token="tok",
            share_type="dynamic",
            allow_list=["*.txt"],
            avoid_list=["*.log"],
            expiry_date="2025-12-31",
        )
        assert ok is True
        share = get_share_by_id(db_conn, "s2")
        assert share["allowed_users"] == ["u1"]
        assert share["secret_token"] == "tok"
        assert share["share_type"] == "dynamic"

    def test_insert_exception(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=Exception("fail"))
        mock_conn.__exit__ = MagicMock(return_value=False)
        ok = insert_share(mock_conn, "s1", "2024", ["/x"])
        assert ok is False


class TestDeleteShare:
    def test_delete_existing(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        delete_share(db_conn, "s1")
        assert get_share_by_id(db_conn, "s1") is None

    def test_delete_nonexistent(self, db_conn):
        delete_share(db_conn, "nope")


class TestUpdateShareMain:
    def testupdate_share_type(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        ok = update_share(db_conn, "s1", share_type="dynamic")
        assert ok is True
        share = get_share_by_id(db_conn, "s1")
        assert share["share_type"] == "dynamic"

    def test_update_disable_token(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"], secret_token="old")
        update_share(db_conn, "s1", disable_token=True)
        share = get_share_by_id(db_conn, "s1")
        assert share["secret_token"] is None

    def test_update_custom_token(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        update_share(db_conn, "s1", secret_token="custom")
        share = get_share_by_id(db_conn, "s1")
        assert share["secret_token"] == "custom"

    def test_update_regenerate_token(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        update_share(db_conn, "s1", disable_token=False)
        share = get_share_by_id(db_conn, "s1")
        assert share["secret_token"] is not None and len(share["secret_token"]) > 10

    def test_update_allow_avoid_lists(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        update_share(db_conn, "s1", allow_list=["*.txt"], avoid_list=["*.log"])
        share = get_share_by_id(db_conn, "s1")
        assert share["allow_list"] == ["*.txt"]
        assert share["avoid_list"] == ["*.log"]

    def test_update_expiry_date(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        update_share(db_conn, "s1", expiry_date="2025-06-30")
        share = get_share_by_id(db_conn, "s1")
        assert share["expiry_date"] == "2025-06-30"

    def test_update_legacy_fields(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        update_share(db_conn, "s1", allowed_users=["alice"], paths=["/b"])
        share = get_share_by_id(db_conn, "s1")
        assert share["allowed_users"] == ["alice"]
        assert share["paths"] == ["/b"]

    def test_update_no_changes(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        assert update_share(db_conn, "s1") is False


class TestGetShareById:
    def test_found(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        share = get_share_by_id(db_conn, "s1")
        assert share["id"] == "s1"

    def test_not_found(self, db_conn):
        assert get_share_by_id(db_conn, "nope") is None

    def test_exception(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("fail")
        assert get_share_by_id(mock_conn, "x") is None


class TestGetAllSharesMain:
    def test_multiple(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a"])
        insert_share(db_conn, "s2", "2024-01-02", ["/b"])
        shares = get_all_shares(db_conn)
        assert len(shares) == 2
        assert "s1" in shares
        assert "s2" in shares

    def test_empty(self, db_conn):
        assert get_all_shares(db_conn) == {}

    def test_exception(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("fail")
        assert get_all_shares(mock_conn) == {}


class TestGetSharesForPath:
    def test_matching(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a.txt", "/b.txt"])
        insert_share(db_conn, "s2", "2024-01-02", ["/c.txt"])
        result = get_shares_for_path(db_conn, "/a.txt")
        assert len(result) == 1
        assert result[0]["id"] == "s1"

    def test_no_match(self, db_conn):
        insert_share(db_conn, "s1", "2024-01-01", ["/a.txt"])
        assert get_shares_for_path(db_conn, "/nope.txt") == []

    def test_exception(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("fail")
        assert get_shares_for_path(mock_conn, "/x") == []


class TestUploadConfigMain:
    def test_load_empty(self, db_conn):
        assert load_upload_config(db_conn) == {}

    def test_save_and_load(self, db_conn):
        save_upload_config(db_conn, {"max_size": 512})
        result = load_upload_config(db_conn)
        assert result == {"max_size": 512}

    def test_save_replaces(self, db_conn):
        save_upload_config(db_conn, {"max_size": 256})
        save_upload_config(db_conn, {"max_size": 1024})
        assert load_upload_config(db_conn) == {"max_size": 1024}


class TestLdapConfigMain:
    def test_create(self, db_conn):
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        assert config["name"] == "Test"
        assert config["active"] is True

    def test_create_duplicate(self, db_conn):
        create_ldap_config(db_conn, "Test", "ldap://a", "dc=a", "member", "uid={u}")
        with pytest.raises(ValueError):
            create_ldap_config(db_conn, "Test", "ldap://b", "dc=b", "member", "uid={u}")

    def test_get_all(self, db_conn):
        create_ldap_config(db_conn, "C1", "ldap://a", "dc=a", "member", "uid={u}")
        create_ldap_config(db_conn, "C2", "ldap://b", "dc=b", "member", "uid={u}")
        configs = get_all_ldap_configs(db_conn)
        assert len(configs) == 2

    def test_get_by_id(self, db_conn):
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={u}"
        )
        result = get_ldap_config_by_id(db_conn, config["id"])
        assert result["name"] == "Test"

    def test_get_by_id_not_found(self, db_conn):
        assert get_ldap_config_by_id(db_conn, 99999) is None

    def test_update(self, db_conn):
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={u}"
        )
        ok = update_ldap_config(db_conn, config["id"], server="ldap://new")
        assert ok is True
        updated = get_ldap_config_by_id(db_conn, config["id"])
        assert updated["server"] == "ldap://new"

    def test_update_active(self, db_conn):
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={u}"
        )
        update_ldap_config(db_conn, config["id"], active=False)
        updated = get_ldap_config_by_id(db_conn, config["id"])
        assert updated["active"] is False

    def test_update_no_fields(self, db_conn):
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={u}"
        )
        assert update_ldap_config(db_conn, config["id"], bad_field="x") is False

    def test_delete(self, db_conn):
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={u}"
        )
        assert delete_ldap_config(db_conn, config["id"]) is True
        assert get_ldap_config_by_id(db_conn, config["id"]) is None

    def test_delete_not_found(self, db_conn):
        assert delete_ldap_config(db_conn, 99999) is False


class TestLdapSyncLogMain:
    def test_log_and_get(self, db_conn):
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={u}"
        )
        log_ldap_sync(db_conn, config["id"], "manual", 10, 5, 2, "success")
        logs = get_ldap_sync_logs(db_conn)
        assert len(logs) == 1
        assert logs[0]["users_found"] == 10

    def test_log_with_error(self, db_conn):
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={u}"
        )
        log_ldap_sync(db_conn, config["id"], "auto", 0, 0, 0, "error", "bind failed")
        logs = get_ldap_sync_logs(db_conn)
        assert logs[0]["error_message"] == "bind failed"

    def test_get_empty(self, db_conn):
        assert get_ldap_sync_logs(db_conn) == []


class TestMakeApp:
    def test_basic_app(self):
        settings = {
            "cookie_secret": "test",
            "xsrf_cookies": False,
        }
        app = make_app(settings)
        assert app is not None

    def test_with_ldap(self):
        settings = {
            "cookie_secret": "test",
            "xsrf_cookies": False,
        }
        app = make_app(
            settings,
            ldap_enabled=True,
            ldap_server="ldap://test",
            ldap_base_dn="dc=test",
            ldap_user_template="uid={u}",
        )
        assert app is not None
        assert settings.get("ldap_server") == "ldap://test"

    def test_with_admin_users(self):
        settings = {
            "cookie_secret": "test",
            "xsrf_cookies": False,
        }
        make_app(settings, admin_users=["admin1"])
        assert settings.get("admin_users") == ["admin1"]


class TestPrintBanner:
    def test_no_error(self):
        print_banner()


class TestAuthenticateInactiveUser:
    def test_inactive_returns_none(self, db_conn):
        create_user(db_conn, "inactive", "pass123")
        update_user(db_conn, 1, active=False)
        result = authenticate_user(db_conn, "inactive", "pass123")
        assert result is None


class TestAssignAdminEdgeCases:
    def test_skips_invalid_entries(self, db_conn):
        create_user(db_conn, "validuser", "pass")
        assign_admin_privileges(db_conn, [None, "", 123, "validuser"])
        user = get_user_by_username(db_conn, "validuser")
        assert user["role"] == "admin"

    def test_already_admin_no_change(self, db_conn):
        create_user(db_conn, "admin", "pass", role="admin")
        assign_admin_privileges(db_conn, ["admin"])
        user = get_user_by_username(db_conn, "admin")
        assert user["role"] == "admin"

    def test_none_conn(self):
        assign_admin_privileges(None, ["user"])

    def test_none_admin_users(self, db_conn):
        assign_admin_privileges(db_conn, None)
