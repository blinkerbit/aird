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

    conn.commit()

    # Seed default policies after the schema is committed so the inserts
    # happen in their own transaction and can fail without rolling back
    # the schema migration above.
    try:
        seed_default_policies(conn)
    except Exception:  # pragma: no cover - defensive
        logger.debug("seed_default_policies failed", exc_info=True)
