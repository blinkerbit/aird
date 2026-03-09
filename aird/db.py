import sqlite3
import traceback
import json
import logging
import secrets
import hashlib
import threading
import time
from datetime import datetime, timezone

# Secure password hashing (Priority 1)
try:
    from argon2 import PasswordHasher
    from argon2 import exceptions as argon2_exceptions

    ARGON2_AVAILABLE = True
    PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
except Exception:
    ARGON2_AVAILABLE = False
    PH = None

try:
    from ldap3 import Server, Connection, ALL

    LDAP3_AVAILABLE = True
except Exception:
    LDAP3_AVAILABLE = False

DB_CONN = None
DB_PATH = "aird.db"
PRAGMA_TABLE_INFO = "PRAGMA table_info(shares)"


def init_db(conn: sqlite3.Connection) -> None:
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
            allowed_users TEXT
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

    # Migration for shares table
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

    conn.commit()


def load_feature_flags(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute("SELECT key, value FROM feature_flags").fetchall()
        return {k: bool(v) for (k, v) in rows}
    except Exception:
        return {}


def save_feature_flags(conn: sqlite3.Connection, flags: dict) -> None:
    try:
        with conn:
            for k, v in flags.items():
                conn.execute(
                    "REPLACE INTO feature_flags (key, value) VALUES (?, ?)",
                    (k, 1 if v else 0),
                )
    except Exception:
        pass


def insert_share(
    conn: sqlite3.Connection,
    sid: str,
    created: str,
    paths: list[str],
    allowed_users: list[str] = None,
    secret_token: str = None,
    share_type: str = "static",
    allow_list: list[str] = None,
    avoid_list: list[str] = None,
    expiry_date: str = None,
) -> bool:
    try:
        with conn:
            conn.execute(
                "REPLACE INTO shares (id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sid,
                    created,
                    json.dumps(paths),
                    json.dumps(allowed_users) if allowed_users else None,
                    secret_token,
                    share_type,
                    json.dumps(allow_list) if allow_list else None,
                    json.dumps(avoid_list) if avoid_list else None,
                    expiry_date,
                ),
            )
        return True
    except Exception as e:
        logging.error(f"Failed to insert share {sid} into database: {e}")
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False


def delete_share(conn: sqlite3.Connection, sid: str) -> None:
    try:
        with conn:
            conn.execute("DELETE FROM shares WHERE id = ?", (sid,))
    except Exception:
        pass


def update_share(
    conn: sqlite3.Connection,
    sid: str,
    share_type: str = None,
    disable_token: bool = None,
    allow_list: list = None,
    avoid_list: list = None,
    secret_token: str = None,
    expiry_date: str = None,
    **kwargs,
) -> bool:
    """Update share information"""
    try:
        updates = []
        values = []

        # Handle new parameters
        if share_type is not None:
            updates.append("share_type = ?")
            values.append(share_type)

        token_update_requested = (disable_token is not None) or (
            secret_token is not None
        )
        if token_update_requested:
            if disable_token is True:
                logging.debug(f"update_share - disable_token={disable_token}")
                logging.debug("Disabling token (setting to None)")
                updates.append("secret_token = ?")
                values.append(None)
            else:
                new_token = (
                    secrets.token_urlsafe(64) if secret_token is None else secret_token
                )
                updates.append("secret_token = ?")
                values.append(new_token)

        if allow_list is not None:
            updates.append("allow_list = ?")
            values.append(json.dumps(allow_list) if allow_list else None)

        if avoid_list is not None:
            updates.append("avoid_list = ?")
            values.append(json.dumps(avoid_list) if avoid_list else None)

        if expiry_date is not None:
            updates.append("expiry_date = ?")
            values.append(expiry_date)

        # Handle legacy parameters
        valid_fields = ["allowed_users", "paths"]
        for field, value in kwargs.items():
            if field in valid_fields:
                updates.append(f"{field} = ?")
                if field in ["allowed_users", "paths"] and value is not None:
                    values.append(json.dumps(value))
                else:
                    values.append(value)

        if not updates:
            return False

        values.append(sid)
        query = f"UPDATE shares SET {', '.join(updates)} WHERE id = ?"

        with conn:
            cursor = conn.execute(query, values)
            logging.debug(f"Update executed, rows affected: {cursor.rowcount}")

        return True
    except Exception as e:
        logging.error(f"Failed to update share {sid}: {e}")
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False


def is_share_expired(expiry_date: str) -> bool:
    """Check if a share has expired based on expiry_date using system time"""
    if not expiry_date:
        return False

    try:
        # Parse the expiry date and convert to naive datetime (system time)
        expiry_datetime = datetime.fromisoformat(expiry_date.replace("Z", ""))

        # Get current system time (naive datetime)
        current_datetime = datetime.now()

        # Simple comparison using system time
        is_expired = current_datetime > expiry_datetime
        logging.debug(
            f"Checking expiry: current={current_datetime}, expiry={expiry_datetime}, expired={is_expired}"
        )
        return is_expired
    except Exception as e:
        logging.error(f"Error checking expiry date {expiry_date}: {e}")
        return False


def cleanup_expired_shares(conn: sqlite3.Connection) -> int:
    """Remove expired shares from the database. Returns the number of shares deleted."""
    try:
        cursor = conn.execute(
            "SELECT id, expiry_date FROM shares WHERE expiry_date IS NOT NULL"
        )
        rows = cursor.fetchall()

        deleted_count = 0
        for share_id, expiry_date in rows:
            if is_share_expired(expiry_date):
                with conn:
                    conn.execute("DELETE FROM shares WHERE id = ?", (share_id,))
                    deleted_count += 1
                    logging.info(f"Deleted expired share: {share_id}")

        return deleted_count
    except Exception as e:
        logging.error(f"Error cleaning up expired shares: {e}")
        return 0


def get_share_by_id(conn: sqlite3.Connection, sid: str) -> dict:
    """Get a single share by ID from database"""
    try:
        logging.debug(f"get_share_by_id called with sid='{sid}'")
        logging.debug(f"conn is {'None' if conn is None else 'available'}")

        # Check if secret_token column exists
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        columns = [row[1] for row in cursor.fetchall()]
        logging.debug(f"Table columns: {columns}")

        if (
            "secret_token" in columns
            and "share_type" in columns
            and "allow_list" in columns
            and "avoid_list" in columns
            and "expiry_date" in columns
        ):
            logging.debug("Using query with all columns including expiry_date")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date FROM shares WHERE id = ?",
                (sid,),
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                (
                    sid,
                    created,
                    paths_json,
                    allowed_users_json,
                    secret_token,
                    share_type,
                    allow_list_json,
                    avoid_list_json,
                    expiry_date,
                ) = row
                result = {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": (
                        json.loads(allowed_users_json) if allowed_users_json else None
                    ),
                    "secret_token": secret_token,
                    "share_type": share_type or "static",
                    "allow_list": (
                        json.loads(allow_list_json) if allow_list_json else []
                    ),
                    "avoid_list": (
                        json.loads(avoid_list_json) if avoid_list_json else []
                    ),
                    "expiry_date": expiry_date,
                }
                logging.debug(f"Returning share data: {result}")
                return result
        elif "secret_token" in columns and "share_type" in columns:
            logging.debug("Using query with secret_token and share_type columns")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token, share_type FROM shares WHERE id = ?",
                (sid,),
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                (
                    sid,
                    created,
                    paths_json,
                    allowed_users_json,
                    secret_token,
                    share_type,
                ) = row
                result = {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": (
                        json.loads(allowed_users_json) if allowed_users_json else None
                    ),
                    "secret_token": secret_token,
                    "share_type": share_type or "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None,
                }
                logging.debug(f"Returning share data: {result}")
                return result
        elif "secret_token" in columns:
            logging.debug("Using query with secret_token column")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares WHERE id = ?",
                (sid,),
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json, secret_token = row
                result = {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": (
                        json.loads(allowed_users_json) if allowed_users_json else None
                    ),
                    "secret_token": secret_token,
                    "share_type": "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None,
                }
                logging.debug(f"Returning share data: {result}")
                return result
        else:
            logging.debug("Using query without secret_token column")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares WHERE id = ?",
                (sid,),
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json = row
                result = {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": (
                        json.loads(allowed_users_json) if allowed_users_json else None
                    ),
                    "secret_token": None,
                    "share_type": "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None,
                }
                logging.debug(f"Returning share data: {result}")
                return result

        logging.debug(f"No share found for sid='{sid}'")
        return None
    except Exception as e:
        logging.error(f"Error getting share {sid}: {e}")
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return None


def get_all_shares(conn: sqlite3.Connection) -> dict:
    """Get all shares from database"""
    try:
        # Check if secret_token column exists
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        columns = [row[1] for row in cursor.fetchall()]

        if "secret_token" in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares"
            )
            shares = {}
            for row in cursor:
                sid, created, paths_json, allowed_users_json, secret_token = row
                shares[sid] = {
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": (
                        json.loads(allowed_users_json) if allowed_users_json else None
                    ),
                    "secret_token": secret_token,
                }
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares"
            )
            shares = {}
            for row in cursor:
                sid, created, paths_json, allowed_users_json = row
                shares[sid] = {
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": (
                        json.loads(allowed_users_json) if allowed_users_json else None
                    ),
                    "secret_token": None,
                }
        return shares
    except Exception as e:
        print(f"Error getting all shares: {e}")
        return {}


def get_shares_for_path(conn: sqlite3.Connection, file_path: str) -> list:
    """Get all shares that contain a specific file path"""
    try:
        # Check if secret_token column exists
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        columns = [row[1] for row in cursor.fetchall()]

        if "secret_token" in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares"
            )
            matching_shares = []
            for row in cursor:
                sid, created, paths_json, allowed_users_json, secret_token = row
                paths = json.loads(paths_json) if paths_json else []
                if file_path in paths:
                    matching_shares.append(
                        {
                            "id": sid,
                            "created": created,
                            "paths": paths,
                            "allowed_users": (
                                json.loads(allowed_users_json)
                                if allowed_users_json
                                else None
                            ),
                            "secret_token": secret_token,
                        }
                    )
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares"
            )
            matching_shares = []
            for row in cursor:
                sid, created, paths_json, allowed_users_json = row
                paths = json.loads(paths_json) if paths_json else []
                if file_path in paths:
                    matching_shares.append(
                        {
                            "id": sid,
                            "created": created,
                            "paths": paths,
                            "allowed_users": (
                                json.loads(allowed_users_json)
                                if allowed_users_json
                                else None
                            ),
                            "secret_token": None,
                        }
                    )
        return matching_shares
    except Exception as e:
        print(f"Error getting shares for path {file_path}: {e}")
        return []


def load_upload_config(conn: sqlite3.Connection) -> dict:
    """Load upload configuration from SQLite database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS upload_config (key TEXT PRIMARY KEY, value INTEGER)"
        )
        rows = conn.execute("SELECT key, value FROM upload_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}


def save_upload_config(conn: sqlite3.Connection, config: dict) -> None:
    """Save upload configuration to SQLite database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS upload_config (key TEXT PRIMARY KEY, value INTEGER)"
        )
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO upload_config (key, value) VALUES (?, ?)",
                    (key, int(value)),
                )
    except Exception:
        pass


def load_allowed_extensions(conn: sqlite3.Connection) -> set:
    """Load allowed upload extensions from database. Returns empty set if table missing or empty."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS upload_allowed_extensions (ext TEXT PRIMARY KEY)"
        )
        rows = conn.execute("SELECT ext FROM upload_allowed_extensions").fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def save_allowed_extensions(conn: sqlite3.Connection, extensions: set) -> None:
    """Replace stored allowed extensions with the given set (e.g. {'.txt', '.pdf'})."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS upload_allowed_extensions (ext TEXT PRIMARY KEY)"
        )
        with conn:
            conn.execute("DELETE FROM upload_allowed_extensions")
            for ext in extensions:
                if ext and isinstance(ext, str) and ext.startswith("."):
                    conn.execute(
                        "INSERT INTO upload_allowed_extensions (ext) VALUES (?)", (ext,)
                    )
    except Exception:
        pass


def load_websocket_config(conn: sqlite3.Connection) -> dict:
    """Load WebSocket configuration from SQLite database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)"
        )
        rows = conn.execute("SELECT key, value FROM websocket_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}


def save_websocket_config(conn: sqlite3.Connection, config: dict) -> None:
    """Save WebSocket configuration to SQLite database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)"
        )
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO websocket_config (key, value) VALUES (?, ?)",
                    (key, int(value)),
                )
    except Exception:
        pass


# ------------------------
# Network shares
# ------------------------


def create_network_share(
    conn: sqlite3.Connection,
    share_id: str,
    name: str,
    folder_path: str,
    protocol: str,
    port: int,
    username: str,
    password: str,
    read_only: bool = False,
) -> bool:
    try:
        created_at = datetime.now().isoformat()
        with conn:
            conn.execute(
                """INSERT INTO network_shares
                   (id, name, folder_path, protocol, port, username, password, enabled, read_only, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    share_id,
                    name,
                    folder_path,
                    protocol,
                    port,
                    username,
                    password,
                    1 if read_only else 0,
                    created_at,
                ),
            )
        return True
    except Exception as e:
        logging.error("Failed to create network share %s: %s", share_id, e)
        return False


def get_all_network_shares(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT id, name, folder_path, protocol, port, username, password, enabled, read_only, created_at "
            "FROM network_shares ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "folder_path": r[2],
                "protocol": r[3],
                "port": r[4],
                "username": r[5],
                "password": r[6],
                "enabled": bool(r[7]),
                "read_only": bool(r[8]),
                "created_at": r[9],
            }
            for r in rows
        ]
    except Exception as e:
        logging.error("Failed to load network shares: %s", e)
        return []


def get_network_share(conn: sqlite3.Connection, share_id: str) -> dict | None:
    try:
        row = conn.execute(
            "SELECT id, name, folder_path, protocol, port, username, password, enabled, read_only, created_at "
            "FROM network_shares WHERE id = ?",
            (share_id,),
        ).fetchone()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "folder_path": row[2],
                "protocol": row[3],
                "port": row[4],
                "username": row[5],
                "password": row[6],
                "enabled": bool(row[7]),
                "read_only": bool(row[8]),
                "created_at": row[9],
            }
        return None
    except Exception as e:
        logging.error("Failed to get network share %s: %s", share_id, e)
        return None


def update_network_share(conn: sqlite3.Connection, share_id: str, **kwargs) -> bool:
    valid_fields = {
        "enabled",
        "read_only",
        "password",
        "port",
        "username",
        "name",
        "folder_path",
        "protocol",
    }
    updates = []
    values = []
    for field, value in kwargs.items():
        if field not in valid_fields:
            continue
        if field in ("enabled", "read_only"):
            updates.append(f"{field} = ?")
            values.append(1 if value else 0)
        else:
            updates.append(f"{field} = ?")
            values.append(value)
    if not updates:
        return False
    values.append(share_id)
    try:
        with conn:
            conn.execute(
                f"UPDATE network_shares SET {', '.join(updates)} WHERE id = ?", values
            )
        return True
    except Exception as e:
        logging.error("Failed to update network share %s: %s", share_id, e)
        return False


def delete_network_share(conn: sqlite3.Connection, share_id: str) -> bool:
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM network_shares WHERE id = ?", (share_id,)
            )
            return cursor.rowcount > 0
    except Exception as e:
        logging.error("Failed to delete network share %s: %s", share_id, e)
        return False


# ------------------------
# Audit log
# ------------------------


def log_audit(
    conn: sqlite3.Connection,
    action: str,
    username: str = None,
    details: str = None,
    ip: str = None,
) -> None:
    """Append an audit log entry. Safe to call with conn=None (no-op)."""
    if conn is None:
        return
    try:
        created = datetime.now(timezone.utc).isoformat() + "Z"
        with conn:
            conn.execute(
                "INSERT INTO audit_log (created_at, username, action, details, ip) VALUES (?, ?, ?, ?, ?)",
                (created, username, action, details, ip),
            )
    except Exception as e:
        logging.warning("Audit log write failed: %s", e)


def get_audit_logs(conn: sqlite3.Connection, limit: int = 500, offset: int = 0) -> list:
    """Return recent audit log entries, newest first."""
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, created_at, username, action, details, ip FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1],
                "username": r[2],
                "action": r[3],
                "details": r[4],
                "ip": r[5],
            }
            for r in rows
        ]
    except Exception:
        return []


# ------------------------
# User management functions
# ------------------------


def hash_password(password: str) -> str:
    """Hash a password using Argon2 (Priority 1). Falls back to Scrypt if Argon2 unavailable."""
    if ARGON2_AVAILABLE and PH is not None:
        return PH.hash(password)
    # Fallback: Scrypt (better than SHA-256)
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1, dklen=32
    )
    return f"scrypt:{salt}:{key.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password supporting Argon2, Scrypt, and legacy salted SHA-256."""
    if not password_hash:
        return False

    # Try Argon2 first
    if password_hash.startswith("$argon2"):
        if ARGON2_AVAILABLE and PH is not None:
            try:
                return PH.verify(password_hash, password)
            except argon2_exceptions.VerifyMismatchError:
                return False
            except Exception:
                return False
        else:
            # Argon2 hash but library not available
            return False

    # Try Scrypt fallback
    if password_hash.startswith("scrypt:"):
        try:
            parts = password_hash.split(":")
            if len(parts) != 3:
                return False
            _, salt, stored_key_hex = parts
            key = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt.encode("utf-8"),
                n=16384,
                r=8,
                p=1,
                dklen=32,
            )
            return secrets.compare_digest(key.hex(), stored_key_hex)
        except Exception:
            return False

    # Legacy format: salt:hash
    try:
        parts = password_hash.split(":", 1)
        if len(parts) != 2:
            return False
        salt, stored_hash = parts
        pwd_hash = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return secrets.compare_digest(pwd_hash, stored_hash)
    except Exception:
        return False


def create_user(
    conn: sqlite3.Connection, username: str, password: str, role: str = "user"
) -> dict:
    """Create a new user in the database"""
    try:
        password_hash = hash_password(password)
        created_at = datetime.now().isoformat()

        with conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, created_at),
            )
            user_id = cursor.lastrowid

        return {
            "id": user_id,
            "username": username,
            "role": role,
            "created_at": created_at,
            "active": True,
            "last_login": None,
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{username}' already exists")
    except Exception as e:
        raise Exception(f"Failed to create user: {str(e)}")


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    """Get user by username"""
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at, active, last_login FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        if row:
            return {
                "id": row[0],
                "username": row[1],
                "password_hash": row[2],
                "role": row[3],
                "created_at": row[4],
                "active": bool(row[5]),
                "last_login": row[6],
            }
        return None
    except Exception:
        return None


def get_all_users(conn: sqlite3.Connection) -> list[dict]:
    """Get all users from the database"""
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, active, last_login FROM users ORDER BY created_at DESC"
        ).fetchall()

        return [
            {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5],
            }
            for row in rows
        ]
    except Exception:
        return []


def search_users(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Search users by username (case-insensitive)"""
    try:
        # Escape SQL LIKE wildcards to prevent injection/information leakage
        # Replace % with \% and _ with \_ to treat them as literal characters
        escaped_query = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        rows = conn.execute(
            "SELECT id, username, role, created_at, active, last_login FROM users WHERE username LIKE ? ESCAPE '\\' AND active = 1 ORDER BY username LIMIT 20",
            (f"%{escaped_query}%",),
        ).fetchall()

        return [
            {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5],
            }
            for row in rows
        ]
    except Exception:
        return []


def update_user(conn: sqlite3.Connection, user_id: int, **kwargs) -> bool:
    """Update user information"""
    try:
        valid_fields = ["username", "password_hash", "role", "active", "last_login"]
        updates = []
        values = []

        for field, value in kwargs.items():
            # Special handling for password - hash it before storing
            if field == "password" and value:
                updates.append("password_hash = ?")
                values.append(hash_password(value))
            elif field in valid_fields:
                if field == "active":
                    updates.append("active = ?")
                    values.append(1 if value else 0)
                else:
                    updates.append(f"{field} = ?")
                    values.append(value)

        if not updates:
            return False

        values.append(user_id)
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"

        with conn:
            conn.execute(query, values)
        return True
    except Exception:
        return False


def create_ldap_config(
    conn: sqlite3.Connection,
    name: str,
    server: str,
    ldap_base_dn: str,
    ldap_member_attributes: str,
    user_template: str,
) -> dict:
    """Create a new LDAP configuration"""
    try:
        created_at = datetime.now().isoformat()

        with conn:
            cursor = conn.execute(
                """INSERT INTO ldap_configs (name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    server,
                    ldap_base_dn,
                    ldap_member_attributes,
                    user_template,
                    created_at,
                ),
            )
            config_id = cursor.lastrowid

        return {
            "id": config_id,
            "name": name,
            "server": server,
            "ldap_base_dn": ldap_base_dn,
            "ldap_member_attributes": ldap_member_attributes,
            "user_template": user_template,
            "created_at": created_at,
            "active": True,
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"LDAP configuration '{name}' already exists")
    except Exception as e:
        raise Exception(f"Failed to create LDAP configuration: {str(e)}")


def get_all_ldap_configs(conn: sqlite3.Connection) -> list[dict]:
    """Get all LDAP configurations"""
    try:
        rows = conn.execute(
            "SELECT id, name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at, active FROM ldap_configs ORDER BY created_at DESC"
        ).fetchall()

        return [
            {
                "id": row[0],
                "name": row[1],
                "server": row[2],
                "ldap_base_dn": row[3],
                "ldap_member_attributes": row[4],
                "user_template": row[5],
                "created_at": row[6],
                "active": bool(row[7]),
            }
            for row in rows
        ]
    except Exception:
        return []


def get_ldap_config_by_id(conn: sqlite3.Connection, config_id: int) -> dict | None:
    """Get LDAP configuration by ID"""
    try:
        row = conn.execute(
            "SELECT id, name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at, active FROM ldap_configs WHERE id = ?",
            (config_id,),
        ).fetchone()

        if row:
            return {
                "id": row[0],
                "name": row[1],
                "server": row[2],
                "ldap_base_dn": row[3],
                "ldap_member_attributes": row[4],
                "user_template": row[5],
                "created_at": row[6],
                "active": bool(row[7]),
            }
        return None
    except Exception:
        return None


def update_ldap_config(conn: sqlite3.Connection, config_id: int, **kwargs) -> bool:
    """Update LDAP configuration"""
    try:
        valid_fields = [
            "name",
            "server",
            "ldap_base_dn",
            "ldap_member_attributes",
            "user_template",
            "active",
        ]
        updates = []
        values = []

        for field, value in kwargs.items():
            if field in valid_fields:
                if field == "active":
                    updates.append("active = ?")
                    values.append(1 if value else 0)
                else:
                    updates.append(f"{field} = ?")
                    values.append(value)

        if not updates:
            return False

        values.append(config_id)
        query = f"UPDATE ldap_configs SET {', '.join(updates)} WHERE id = ?"

        with conn:
            conn.execute(query, values)
        return True
    except Exception:
        return False


def delete_ldap_config(conn: sqlite3.Connection, config_id: int) -> bool:
    """Delete LDAP configuration"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM ldap_configs WHERE id = ?", (config_id,))
            return cursor.rowcount > 0
    except Exception:
        return False


def log_ldap_sync(
    conn: sqlite3.Connection,
    config_id: int,
    sync_type: str,
    users_found: int,
    users_created: int,
    users_removed: int,
    status: str,
    error_message: str = None,
) -> None:
    """Log LDAP synchronization results"""
    try:
        sync_time = datetime.now().isoformat()
        with conn:
            conn.execute(
                """INSERT INTO ldap_sync_log (config_id, sync_type, users_found, users_created, users_removed, sync_time, status, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    config_id,
                    sync_type,
                    users_found,
                    users_created,
                    users_removed,
                    sync_time,
                    status,
                    error_message,
                ),
            )
    except Exception:
        pass  # Don't fail the sync if logging fails


def get_ldap_sync_logs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Get recent LDAP sync logs"""
    try:
        rows = conn.execute(
            """SELECT l.id, l.config_id, c.name, l.sync_type, l.users_found, l.users_created, l.users_removed, 
                      l.sync_time, l.status, l.error_message
               FROM ldap_sync_log l
               JOIN ldap_configs c ON l.config_id = c.id
               ORDER BY l.sync_time DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        return [
            {
                "id": row[0],
                "config_id": row[1],
                "config_name": row[2],
                "sync_type": row[3],
                "users_found": row[4],
                "users_created": row[5],
                "users_removed": row[6],
                "sync_time": row[7],
                "status": row[8],
                "error_message": row[9],
            }
            for row in rows
        ]
    except Exception:
        return []


def sync_ldap_users(conn: sqlite3.Connection) -> dict:
    """Synchronize users from all active LDAP configurations"""
    if not conn:
        return {"status": "error", "message": "Database not available"}

    try:
        # Get all active LDAP configurations
        configs = get_all_ldap_configs(conn)
        active_configs = [c for c in configs if c["active"]]

        if not active_configs:
            return {
                "status": "success",
                "message": "No active LDAP configurations found",
            }

        all_ldap_users = set()  # Use set to automatically handle duplicates
        sync_results = []

        for config in active_configs:
            try:
                server = Server(config["server"], get_info=ALL)
                conn_ldap = Connection(server)

                if not conn_ldap.bind():
                    sync_results.append(
                        {
                            "config_name": config["name"],
                            "status": "error",
                            "message": f"Failed to bind to LDAP server: {config['server']}",
                        }
                    )
                    continue

                # Search for groups and their members
                search_filter = "(objectClass=groupOfNames)"
                conn_ldap.search(
                    search_base=config["ldap_base_dn"],
                    search_filter=search_filter,
                    attributes=[config["ldap_member_attributes"]],
                )

                config_users = set()
                for entry in conn_ldap.entries:
                    if hasattr(entry, config["ldap_member_attributes"]):
                        members = getattr(entry, config["ldap_member_attributes"])
                        if members:
                            for member in members:
                                # Extract username using the user template
                                username = extract_username_from_dn(
                                    member, config["user_template"]
                                )
                                if username:
                                    config_users.add(username)
                                    all_ldap_users.add(username)

                sync_results.append(
                    {
                        "config_name": config["name"],
                        "status": "success",
                        "users_found": len(config_users),
                    }
                )

                # Log the sync for this config
                log_ldap_sync(
                    conn, config["id"], "group_sync", len(config_users), 0, 0, "success"
                )

            except Exception as e:
                sync_results.append(
                    {
                        "config_name": config["name"],
                        "status": "error",
                        "message": str(e),
                    }
                )
                log_ldap_sync(
                    conn, config["id"], "group_sync", 0, 0, 0, "error", str(e)
                )

        # Now sync users with the database
        users_created = 0
        users_removed = 0

        # Get current users from database
        current_users = get_all_users(conn)
        current_usernames = {user["username"] for user in current_users}

        # Create new users that are in LDAP but not in database
        for username in all_ldap_users:
            if username not in current_usernames:
                try:
                    # Create user with a dummy password (they'll authenticate via LDAP)
                    create_user(conn, username, "ldap_user", role="user")
                    users_created += 1
                except Exception as e:
                    print(f"Failed to create user {username}: {e}")

        # Remove users that are in database but not in LDAP
        for user in current_users:
            if user["username"] not in all_ldap_users and user["username"] != "admin":
                try:
                    delete_user(conn, user["id"])
                    users_removed += 1
                except Exception as e:
                    print(f"Failed to remove user {user['username']}: {e}")

        return {
            "status": "success",
            "total_ldap_users": len(all_ldap_users),
            "users_created": users_created,
            "users_removed": users_removed,
            "config_results": sync_results,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


def extract_username_from_dn(dn: str, user_template: str) -> str | None:
    """Extract username from LDAP DN using the user template"""
    try:
        # Simple template matching - can be enhanced for more complex patterns
        if "{username}" in user_template:
            # For templates like "uid={username},ou=users,dc=example,dc=com"
            # We need to extract the actual username from the DN
            # This is a simplified implementation
            parts = dn.split(",")
            for part in parts:
                if "=" in part:
                    key, value = part.split("=", 1)
                    if key.strip() in ["uid", "cn", "sAMAccountName"]:
                        return value.strip()
        return None
    except Exception:
        return None


def start_ldap_sync_scheduler(conn: sqlite3.Connection) -> None:
    """Start the daily LDAP sync scheduler in a background thread"""

    def sync_worker():
        while True:
            try:
                # Run sync at 2 AM every day
                current_time = datetime.now()
                if current_time.hour == 2 and current_time.minute == 0:
                    print("Starting daily LDAP sync...")
                    sync_result = sync_ldap_users(conn)
                    if sync_result["status"] == "success":
                        print(
                            f"Daily LDAP sync completed: {sync_result.get('total_ldap_users', 0)} users found, {sync_result.get('users_created', 0)} created, {sync_result.get('users_removed', 0)} removed"
                        )
                    else:
                        print(
                            f"Daily LDAP sync failed: {sync_result.get('message', 'Unknown error')}"
                        )

                # Sleep for 1 minute to avoid busy waiting
                time.sleep(60)
            except Exception as e:
                print(f"Error in LDAP sync scheduler: {e}")
                time.sleep(300)  # Sleep for 5 minutes on error

    if conn:
        sync_thread = threading.Thread(target=sync_worker, daemon=True)
        sync_thread.start()
        print("LDAP sync scheduler started (daily at 2 AM)")


def assign_admin_privileges(conn: sqlite3.Connection, admin_users: list) -> None:
    """Assign admin privileges to users listed in admin_users configuration"""
    if not admin_users or not conn:
        return

    try:
        for username in admin_users:
            if not username or not isinstance(username, str):
                continue

            # Check if user exists
            user = get_user_by_username(conn, username)
            if user:
                # Update user role to admin if not already admin
                if user["role"] != "admin":
                    update_user(conn, user["id"], role="admin")
                    print(
                        f"ADMIN: Assigned admin privileges to existing user '{username}'"
                    )
            else:
                print(
                    f"ADMIN: User '{username}' not found in database - will be assigned admin privileges on first login"
                )
    except Exception as e:
        print(f"ADMIN: Warning: Failed to assign admin privileges: {e}")


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """Delete a user from the database"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0
    except Exception:
        return False


def authenticate_user(
    conn: sqlite3.Connection, username: str, password: str
) -> dict | None:
    """Authenticate a user and update last_login"""
    user = get_user_by_username(conn, username)
    if user and user["active"] and verify_password(password, user["password_hash"]):
        # Update last login timestamp
        update_user(conn, user["id"], last_login=datetime.now().isoformat())
        # Remove sensitive information before returning
        del user["password_hash"]
        return user
    return None
