"""Database schema creation and migration."""

import sqlite3
import logging

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

    conn.commit()
