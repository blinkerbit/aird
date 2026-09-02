"""Database schema creation and migration."""

import sqlite3
import logging

from aird.db.policy_seeds import seed_default_policies

logger = logging.getLogger(__name__)

PRAGMA_TABLE_INFO = "PRAGMA table_info(shares)"


def init_db(conn: sqlite3.Connection) -> None:
    # Enable Write-Ahead Logging (WAL) for high concurrency
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feature_flags (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shares (
            id TEXT PRIMARY KEY,
            created TEXT NOT NULL,
            paths TEXT NOT NULL,
            allowed_users TEXT,
            modify_users TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_login TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ldap_configs (
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
        CREATE TABLE IF NOT EXISTS ldap_sync_log (
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

    cursor = conn.cursor()
    cursor.execute(PRAGMA_TABLE_INFO)
    columns = [column[1] for column in cursor.fetchall()]
    if "allowed_users" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN allowed_users TEXT")
    if "secret_token" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN secret_token TEXT")
    if "share_type" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN share_type TEXT DEFAULT 'static'")
    if "allow_list" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN allow_list TEXT")
    if "avoid_list" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN avoid_list TEXT")
    if "expiry_date" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN expiry_date TEXT")
    if "modify_users" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN modify_users TEXT")
    if "tag_name" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN tag_name TEXT")
    if "created_by" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN created_by TEXT")

    cursor.execute("PRAGMA table_info(users)")
    user_columns = [column[1] for column in cursor.fetchall()]
    if user_columns and "must_change_password" not in user_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            username TEXT,
            action TEXT NOT NULL,
            details TEXT,
            ip TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS network_shares (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            protocol TEXT NOT NULL DEFAULT 'webdav',
            port INTEGER NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            read_only INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            file_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(username, file_path)
        )
        """)

    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "quota_bytes" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN quota_bytes INTEGER")
    if "used_bytes" not in user_cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN used_bytes INTEGER NOT NULL DEFAULT 0"
        )

    # ABAC tables (additive only; safe to leave in place even when the engine is off).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_attributes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(username, key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT NOT NULL,
            glob_pattern TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            created_by TEXT,
            UNIQUE(tag, glob_pattern)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_colors (
            tag TEXT PRIMARY KEY,
            color TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            effect TEXT NOT NULL,
            target_actions TEXT NOT NULL,
            condition_json TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            username TEXT,
            action TEXT NOT NULL,
            resource TEXT,
            decision TEXT NOT NULL,
            reason TEXT,
            policy_id INTEGER,
            attributes_json TEXT,
            ip TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_decisions_created_at "
        "ON policy_decisions(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_decisions_username "
        "ON policy_decisions(username)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ranged_upload_sessions (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            upload_dir TEXT NOT NULL,
            filename TEXT NOT NULL,
            temp_path TEXT NOT NULL,
            total_size INTEGER NOT NULL,
            ranges_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
        """)
    ranged_cols = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(ranged_upload_sessions)"
        ).fetchall()
    }
    if "transfer_profile" not in ranged_cols:
        conn.execute(
            "ALTER TABLE ranged_upload_sessions "
            "ADD COLUMN transfer_profile TEXT NOT NULL DEFAULT 'open'"
        )
    if "chunk_bytes" not in ranged_cols:
        conn.execute(
            "ALTER TABLE ranged_upload_sessions "
            "ADD COLUMN chunk_bytes INTEGER NOT NULL DEFAULT 94371840"
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upload_config (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS server_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            user_role TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_active_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT
        )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_sessions_username "
        "ON user_sessions(username)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_sessions_last_active "
        "ON user_sessions(last_active_at)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upload_allowed_extensions (
            ext TEXT PRIMARY KEY
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS websocket_config (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS webauthn_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            credential_id TEXT NOT NULL UNIQUE,
            public_key BLOB NOT NULL,
            sign_count INTEGER NOT NULL DEFAULT 0,
            transports TEXT,
            aaguid TEXT,
            prf_capable INTEGER NOT NULL DEFAULT 0,
            nickname TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS webauthn_challenges (
            challenge TEXT PRIMARY KEY,
            username TEXT,
            purpose TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_members (
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (conversation_id, user_id),
            FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            msg_type TEXT NOT NULL,
            body TEXT,
            metadata_json TEXT,
            attachment_path TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id)
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_read_state (
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_read_message_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (conversation_id, user_id)
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_file_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            relative_path TEXT NOT NULL,
            original_name TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
        ON chat_messages (conversation_id, id)
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_e2e_keys (
            username TEXT PRIMARY KEY,
            public_jwk TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gitlab_bindings (
            owner_username TEXT NOT NULL,
            folder_rel_path TEXT NOT NULL,
            gitlab_host TEXT NOT NULL,
            code_project TEXT NOT NULL,
            issues_project TEXT NOT NULL,
            board_iid INTEGER,
            repo_path_prefix TEXT,
            updated_by TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_username, folder_rel_path)
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_comments (
            id TEXT PRIMARY KEY,
            owner_username TEXT NOT NULL,
            file_rel_path TEXT NOT NULL,
            author_username TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            edited_at TEXT,
            gitlab_issue_iid INTEGER,
            gitlab_note_id INTEGER
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_file_comments_path
        ON file_comments (owner_username, file_rel_path, created_at)
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gitlab_issue_cache (
            owner_username TEXT NOT NULL,
            project_key TEXT NOT NULL,
            issue_iid INTEGER NOT NULL,
            title TEXT NOT NULL,
            web_url TEXT,
            state TEXT NOT NULL DEFAULT 'opened',
            paths_json TEXT NOT NULL DEFAULT '[]',
            assignees_json TEXT NOT NULL DEFAULT '[]',
            labels_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_username, project_key, issue_iid)
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gitlab_issue_cache_owner_project
        ON gitlab_issue_cache (owner_username, project_key)
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plugin_access (
            plugin_id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            usernames_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS onedrive_backup_rules (
            username TEXT PRIMARY KEY,
            paths_json TEXT NOT NULL DEFAULT '[]',
            include_untracked INTEGER NOT NULL DEFAULT 1,
            include_aird_config INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS onedrive_folder_maps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            local_path TEXT NOT NULL,
            remote_path TEXT NOT NULL,
            ignore_extra TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(username, local_path)
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_onedrive_folder_maps_user
        ON onedrive_folder_maps (username)
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS onedrive_sync_state (
            username TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            local_mtime REAL NOT NULL,
            local_size INTEGER NOT NULL,
            remote_item_id TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (username, rel_path)
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS onedrive_sync_status (
            username TEXT PRIMARY KEY,
            last_started_at TEXT,
            last_finished_at TEXT,
            last_ok INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            uploaded INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_plugin_secrets (
            username TEXT NOT NULL,
            secret_key TEXT NOT NULL,
            ciphertext TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (username, secret_key)
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_path_mounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            host_path TEXT NOT NULL,
            mount_name TEXT NOT NULL,
            writable INTEGER NOT NULL DEFAULT 1,
            created_by TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(username, mount_name)
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_path_mounts_user
        ON user_path_mounts (username)
        """)

    conn.commit()

    # Seed default policies after the schema is committed so the inserts
    # happen in their own transaction and can fail without rolling back
    # the schema migration above.
    try:
        seed_default_policies(conn)
    except Exception:  # pragma: no cover - defensive
        logger.debug("seed_default_policies failed", exc_info=True)
