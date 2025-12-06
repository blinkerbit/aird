"""Tests for aird/database/ldap.py"""

import pytest
import sqlite3
from datetime import datetime
from unittest.mock import patch, MagicMock

from aird.database.ldap import (
    create_ldap_config,
    get_all_ldap_configs,
    get_ldap_config_by_id,
    update_ldap_config,
    delete_ldap_config,
    log_ldap_sync,
    get_ldap_sync_logs,
    extract_username_from_dn,
    sync_ldap_users,
)


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database with required tables"""
    conn = sqlite3.connect(":memory:")
    
    # Create required tables
    conn.execute("""
        CREATE TABLE ldap_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            server TEXT NOT NULL,
            ldap_base_dn TEXT NOT NULL,
            ldap_member_attributes TEXT NOT NULL DEFAULT 'member',
            user_template TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE ldap_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER NOT NULL,
            sync_type TEXT NOT NULL,
            users_found INTEGER NOT NULL,
            users_created INTEGER NOT NULL,
            users_removed INTEGER NOT NULL,
            sync_time TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT,
            FOREIGN KEY (config_id) REFERENCES ldap_configs (id)
        )
    """)
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_login TEXT
        )
    """)
    conn.commit()
    
    yield conn
    conn.close()


class TestCreateLdapConfig:
    """Tests for create_ldap_config function"""
    
    def test_create_basic_config(self, db_conn):
        """Test creating a basic LDAP config"""
        config = create_ldap_config(
            db_conn,
            name="Corporate LDAP",
            server="ldap://ldap.example.com",
            ldap_base_dn="dc=example,dc=com",
            ldap_member_attributes="member",
            user_template="uid={username},ou=users,dc=example,dc=com"
        )
        
        assert config['id'] is not None
        assert config['name'] == "Corporate LDAP"
        assert config['server'] == "ldap://ldap.example.com"
        assert config['ldap_base_dn'] == "dc=example,dc=com"
        assert config['ldap_member_attributes'] == "member"
        assert config['user_template'] == "uid={username},ou=users,dc=example,dc=com"
        assert config['active'] is True
    
    def test_create_duplicate_name_raises_error(self, db_conn):
        """Test that duplicate name raises ValueError"""
        create_ldap_config(
            db_conn, "Test Config", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        with pytest.raises(ValueError, match="already exists"):
            create_ldap_config(
                db_conn, "Test Config", "ldap://b", "dc=b", "member", "uid={username}"
            )
    
    def test_created_at_is_set(self, db_conn):
        """Test that created_at timestamp is set"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        assert config['created_at'] is not None
        # Should be a valid ISO format date
        datetime.fromisoformat(config['created_at'])


class TestGetAllLdapConfigs:
    """Tests for get_all_ldap_configs function"""
    
    def test_get_empty_configs(self, db_conn):
        """Test getting configs when none exist"""
        configs = get_all_ldap_configs(db_conn)
        assert configs == []
    
    def test_get_multiple_configs(self, db_conn):
        """Test getting multiple configs"""
        create_ldap_config(db_conn, "Config1", "ldap://a", "dc=a", "member", "uid={username}")
        create_ldap_config(db_conn, "Config2", "ldap://b", "dc=b", "member", "uid={username}")
        create_ldap_config(db_conn, "Config3", "ldap://c", "dc=c", "member", "uid={username}")
        
        configs = get_all_ldap_configs(db_conn)
        assert len(configs) == 3
    
    def test_configs_ordered_by_created_at_desc(self, db_conn):
        """Test that configs are ordered by created_at descending"""
        create_ldap_config(db_conn, "First", "ldap://a", "dc=a", "member", "uid={username}")
        create_ldap_config(db_conn, "Second", "ldap://b", "dc=b", "member", "uid={username}")
        
        configs = get_all_ldap_configs(db_conn)
        # Most recent should be first
        assert configs[0]['name'] == "Second"
        assert configs[1]['name'] == "First"


class TestGetLdapConfigById:
    """Tests for get_ldap_config_by_id function"""
    
    def test_get_existing_config(self, db_conn):
        """Test getting an existing config by ID"""
        created = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        config = get_ldap_config_by_id(db_conn, created['id'])
        
        assert config is not None
        assert config['name'] == "Test"
        assert config['server'] == "ldap://a"
    
    def test_get_nonexistent_config(self, db_conn):
        """Test getting a non-existent config returns None"""
        config = get_ldap_config_by_id(db_conn, 999)
        assert config is None
    
    def test_active_field_is_boolean(self, db_conn):
        """Test that active field is returned as boolean"""
        created = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        config = get_ldap_config_by_id(db_conn, created['id'])
        assert isinstance(config['active'], bool)


class TestUpdateLdapConfig:
    """Tests for update_ldap_config function"""
    
    def test_update_server(self, db_conn):
        """Test updating server field"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://old", "dc=a", "member", "uid={username}"
        )
        
        result = update_ldap_config(db_conn, config['id'], server="ldap://new")
        
        assert result is True
        updated = get_ldap_config_by_id(db_conn, config['id'])
        assert updated['server'] == "ldap://new"
    
    def test_update_multiple_fields(self, db_conn):
        """Test updating multiple fields"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        result = update_ldap_config(
            db_conn, config['id'],
            server="ldap://new",
            ldap_base_dn="dc=new,dc=com"
        )
        
        assert result is True
        updated = get_ldap_config_by_id(db_conn, config['id'])
        assert updated['server'] == "ldap://new"
        assert updated['ldap_base_dn'] == "dc=new,dc=com"
    
    def test_update_active_status(self, db_conn):
        """Test updating active status"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        result = update_ldap_config(db_conn, config['id'], active=False)
        
        assert result is True
        updated = get_ldap_config_by_id(db_conn, config['id'])
        assert updated['active'] is False
    
    def test_update_no_fields_returns_false(self, db_conn):
        """Test that updating with no valid fields returns False"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        result = update_ldap_config(db_conn, config['id'])
        assert result is False
    
    def test_update_invalid_field_ignored(self, db_conn):
        """Test that invalid fields are ignored"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        result = update_ldap_config(db_conn, config['id'], invalid_field="value")
        assert result is False


class TestDeleteLdapConfig:
    """Tests for delete_ldap_config function"""
    
    def test_delete_existing_config(self, db_conn):
        """Test deleting an existing config"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        result = delete_ldap_config(db_conn, config['id'])
        
        assert result is True
        assert get_ldap_config_by_id(db_conn, config['id']) is None
    
    def test_delete_nonexistent_config(self, db_conn):
        """Test deleting non-existent config returns False"""
        result = delete_ldap_config(db_conn, 999)
        assert result is False


class TestLogLdapSync:
    """Tests for log_ldap_sync function"""
    
    def test_log_successful_sync(self, db_conn):
        """Test logging a successful sync"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        # Should not raise
        log_ldap_sync(
            db_conn,
            config_id=config['id'],
            sync_type="manual",
            users_found=100,
            users_created=10,
            users_removed=5,
            status="success"
        )
    
    def test_log_failed_sync_with_error(self, db_conn):
        """Test logging a failed sync with error message"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        # Should not raise
        log_ldap_sync(
            db_conn,
            config_id=config['id'],
            sync_type="scheduled",
            users_found=0,
            users_created=0,
            users_removed=0,
            status="error",
            error_message="Connection failed"
        )


class TestGetLdapSyncLogs:
    """Tests for get_ldap_sync_logs function"""
    
    def test_get_empty_logs(self, db_conn):
        """Test getting logs when none exist"""
        logs = get_ldap_sync_logs(db_conn)
        assert logs == []
    
    def test_get_logs_with_limit(self, db_conn):
        """Test getting logs with limit"""
        config = create_ldap_config(
            db_conn, "Test", "ldap://a", "dc=a", "member", "uid={username}"
        )
        
        for i in range(10):
            log_ldap_sync(db_conn, config['id'], "manual", i, 0, 0, "success")
        
        logs = get_ldap_sync_logs(db_conn, limit=5)
        assert len(logs) == 5
    
    def test_logs_include_config_name(self, db_conn):
        """Test that logs include config name"""
        config = create_ldap_config(
            db_conn, "My Config", "ldap://a", "dc=a", "member", "uid={username}"
        )
        log_ldap_sync(db_conn, config['id'], "manual", 10, 5, 2, "success")
        
        logs = get_ldap_sync_logs(db_conn)
        assert len(logs) == 1
        assert logs[0]['config_name'] == "My Config"


class TestExtractUsernameFromDn:
    """Tests for extract_username_from_dn function"""
    
    def test_extract_from_uid(self):
        """Test extracting username from uid attribute"""
        dn = "uid=john.doe,ou=People,dc=example,dc=com"
        template = "uid={username},ou=People,dc=example,dc=com"
        
        result = extract_username_from_dn(dn, template)
        assert result == "john.doe"
    
    def test_extract_from_cn(self):
        """Test extracting username from cn attribute"""
        dn = "cn=John Doe,ou=People,dc=example,dc=com"
        template = "cn={username},ou=People,dc=example,dc=com"
        
        result = extract_username_from_dn(dn, template)
        assert result == "John Doe"
    
    def test_extract_from_samaccountname(self):
        """Test extracting username from sAMAccountName attribute"""
        dn = "sAMAccountName=jdoe,cn=Users,dc=corp,dc=local"
        template = "sAMAccountName={username},cn=Users,dc=corp,dc=local"
        
        result = extract_username_from_dn(dn, template)
        assert result == "jdoe"
    
    def test_extract_with_spaces_in_value(self):
        """Test extracting username with spaces"""
        dn = "cn=John William Doe,ou=People,dc=example,dc=com"
        template = "cn={username},ou=People,dc=example,dc=com"
        
        result = extract_username_from_dn(dn, template)
        assert result == "John William Doe"
    
    def test_no_username_placeholder_returns_none(self):
        """Test that template without {username} returns None"""
        dn = "uid=john,ou=People,dc=example,dc=com"
        template = "ou=People,dc=example,dc=com"
        
        result = extract_username_from_dn(dn, template)
        assert result is None
    
    def test_no_matching_attribute_returns_none(self):
        """Test that DN without matching attribute returns None"""
        dn = "ou=People,dc=example,dc=com"
        template = "uid={username},ou=People,dc=example,dc=com"
        
        result = extract_username_from_dn(dn, template)
        assert result is None
    
    def test_invalid_dn_returns_none(self):
        """Test that invalid DN returns None"""
        result = extract_username_from_dn("", "uid={username}")
        assert result is None


class TestSyncLdapUsers:
    """Tests for sync_ldap_users function"""
    
    def test_sync_with_no_connection(self):
        """Test sync with no database connection"""
        result = sync_ldap_users(None)
        
        assert result['status'] == "error"
        assert "Database not available" in result['message']
    
    def test_sync_with_no_active_configs(self, db_conn):
        """Test sync when no active configs exist"""
        result = sync_ldap_users(db_conn)
        
        assert result['status'] == "success"
        assert "No active LDAP configurations found" in result['message']
    
    @patch('aird.database.ldap.Server')
    @patch('aird.database.ldap.Connection')
    def test_sync_ldap_bind_failure(self, mock_conn_class, mock_server_class, db_conn):
        """Test sync when LDAP bind fails"""
        create_ldap_config(
            db_conn, "Test", "ldap://test", "dc=test", "member", "uid={username}"
        )
        
        mock_conn = MagicMock()
        mock_conn.bind.return_value = False
        mock_conn_class.return_value = mock_conn
        
        result = sync_ldap_users(db_conn)
        
        assert result['status'] == "success"
        assert len(result['config_results']) == 1
        assert result['config_results'][0]['status'] == "error"
