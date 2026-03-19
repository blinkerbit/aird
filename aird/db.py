import sqlite3
import traceback
import json
import logging
import secrets
import hashlib
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
    import ldap3  # noqa: F401

    LDAP3_AVAILABLE = True
except ImportError:
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            file_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(username, file_path)
        )
        """)

    # Migrate users table: add quota columns if missing
    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "quota_bytes" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN quota_bytes INTEGER")
    if "used_bytes" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN used_bytes INTEGER NOT NULL DEFAULT 0")

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


def _get_token_field_updates(
    disable_token: bool | None, secret_token: str | None
) -> tuple[list, list]:
    """Return (SET clauses, values) for secret_token update."""
    if disable_token is None and secret_token is None:
        return [], []
    updates, values = [], []
    if disable_token is True:
        updates.append("secret_token = ?")
        values.append(None)
    else:
        new_token = secrets.token_urlsafe(64) if secret_token is None else secret_token
        updates.append("secret_token = ?")
        values.append(new_token)
    return updates, values


def _get_kwargs_field_updates(kwargs: dict) -> tuple[list, list]:
    """Return (SET clauses, values) for legacy allowed_users/paths kwargs."""
    updates, values = [], []
    for field, value in kwargs.items():
        if field in ("allowed_users", "paths"):
            updates.append(f"{field} = ?")
            values.append(json.dumps(value) if value is not None else value)
    return updates, values


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
        updates, values = [], []

        if share_type is not None:
            updates.append("share_type = ?")
            values.append(share_type)

        token_updates, token_values = _get_token_field_updates(
            disable_token, secret_token
        )
        updates.extend(token_updates)
        values.extend(token_values)

        if allow_list is not None:
            updates.append("allow_list = ?")
            values.append(json.dumps(allow_list) if allow_list else None)

        if avoid_list is not None:
            updates.append("avoid_list = ?")
            values.append(json.dumps(avoid_list) if avoid_list else None)

        if expiry_date is not None:
            updates.append("expiry_date = ?")
            values.append(expiry_date)

        kwarg_updates, kwarg_values = _get_kwargs_field_updates(kwargs)
        updates.extend(kwarg_updates)
        values.extend(kwarg_values)

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


_SHARE_BASE_COLS = ["id", "created", "paths", "allowed_users"]
_SHARE_OPTIONAL_COLS = [
    "secret_token",
    "share_type",
    "allow_list",
    "avoid_list",
    "expiry_date",
]


def _get_share_col_names(available_columns: list) -> list:
    """Return column list for SELECT based on available schema columns."""
    return _SHARE_BASE_COLS + [
        c for c in _SHARE_OPTIONAL_COLS if c in available_columns
    ]


def _row_to_share_dict(row: tuple, col_names: list) -> dict:
    """Convert a raw share DB row to a normalized share dict."""
    d = dict(zip(col_names, row))
    return {
        "id": d["id"],
        "created": d["created"],
        "paths": json.loads(d["paths"]) if d.get("paths") else [],
        "allowed_users": (
            json.loads(d["allowed_users"]) if d.get("allowed_users") else None
        ),
        "secret_token": d.get("secret_token"),
        "share_type": d.get("share_type") or "static",
        "allow_list": json.loads(d["allow_list"]) if d.get("allow_list") else [],
        "avoid_list": json.loads(d["avoid_list"]) if d.get("avoid_list") else [],
        "expiry_date": d.get("expiry_date"),
    }


def get_share_by_id(conn: sqlite3.Connection, sid: str) -> dict | None:
    """Get a single share by ID from database"""
    try:
        logging.debug(f"get_share_by_id called with sid='{sid}'")
        logging.debug(f"conn is {'None' if conn is None else 'available'}")
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        available = [row[1] for row in cursor.fetchall()]
        logging.debug(f"Table columns: {available}")
        col_names = _get_share_col_names(available)
        cursor = conn.execute(
            f"SELECT {', '.join(col_names)} FROM shares WHERE id = ?", (sid,)
        )
        row = cursor.fetchone()
        if row:
            result = _row_to_share_dict(row, col_names)
            logging.debug(f"Returning share data: {result}")
            return result
        logging.debug(f"No share found for sid='{sid}'")
        return None
    except Exception as e:  # NOSONAR
        logging.error(f"Error getting share {sid}: {e}")
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return None


def get_all_shares(conn: sqlite3.Connection) -> dict:
    """Get all shares from database"""
    try:
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        available = [row[1] for row in cursor.fetchall()]
        col_names = _get_share_col_names(available)
        cursor = conn.execute(f"SELECT {', '.join(col_names)} FROM shares")
        return {row[0]: _row_to_share_dict(row, col_names) for row in cursor}
    except Exception as e:
        print(f"Error getting all shares: {e}")
        return {}


def get_shares_for_path(conn: sqlite3.Connection, file_path: str) -> list:
    """Get all shares that contain a specific file path"""
    try:
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        available = [row[1] for row in cursor.fetchall()]
        col_names = _get_share_col_names(available)
        cursor = conn.execute(f"SELECT {', '.join(col_names)} FROM shares")
        matching = []
        for row in cursor:
            share = _row_to_share_dict(row, col_names)
            if file_path in share["paths"]:
                matching.append(share)
        return matching
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


def get_user_quota(conn: sqlite3.Connection, username: str) -> dict:
    """Return quota info for a user: {quota_bytes, used_bytes}."""
    if conn is None:
        return {"quota_bytes": None, "used_bytes": 0}
    try:
        row = conn.execute(
            "SELECT quota_bytes, used_bytes FROM users WHERE username=?",
            (username,),
        ).fetchone()
        if row:
            return {"quota_bytes": row[0], "used_bytes": row[1] or 0}
    except Exception:
        pass
    return {"quota_bytes": None, "used_bytes": 0}


def update_user_used_bytes(conn: sqlite3.Connection, username: str, delta: int) -> None:
    """Add delta bytes to a user's used_bytes (can be negative for deletes)."""
    if conn is None:
        return
    try:
        with conn:
            conn.execute(
                "UPDATE users SET used_bytes = MAX(0, COALESCE(used_bytes, 0) + ?) WHERE username=?",
                (delta, username),
            )
    except Exception:
        pass


def set_user_quota(conn: sqlite3.Connection, username: str, quota_bytes) -> None:
    """Set quota_bytes for a user. None means unlimited."""
    if conn is None:
        return
    try:
        with conn:
            conn.execute(
                "UPDATE users SET quota_bytes=? WHERE username=?",
                (quota_bytes, username),
            )
    except Exception:
        pass


def toggle_favorite(conn: sqlite3.Connection, username: str, file_path: str) -> bool:
    """Toggle a favorite for a user. Returns True if now favorited, False if removed."""
    if conn is None:
        return False
    row = conn.execute(
        "SELECT id FROM favorites WHERE username=? AND file_path=?",
        (username, file_path),
    ).fetchone()
    if row:
        with conn:
            conn.execute("DELETE FROM favorites WHERE id=?", (row[0],))
        return False
    else:
        created = datetime.now(timezone.utc).isoformat() + "Z"
        with conn:
            conn.execute(
                "INSERT INTO favorites (username, file_path, created_at) VALUES (?, ?, ?)",
                (username, file_path, created),
            )
        return True


def get_user_favorites(conn: sqlite3.Connection, username: str) -> list:
    """Return list of favorited file paths for a user."""
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT file_path FROM favorites WHERE username=? ORDER BY created_at DESC",
            (username,),
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def get_share_download_count(conn: sqlite3.Connection, share_id: str) -> int:
    """Return the number of share_download audit events for a given share_id."""
    if conn is None:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='share_download' AND details LIKE ?",
            (f"share_id={share_id}%",),
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


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


def _verify_argon2(password: str, password_hash: str) -> bool:
    """Verify an Argon2 password hash."""
    if not (ARGON2_AVAILABLE and PH is not None):
        return False
    try:
        return PH.verify(password_hash, password)
    except (argon2_exceptions.VerifyMismatchError, Exception):
        return False


def _verify_scrypt(password: str, password_hash: str) -> bool:
    """Verify a scrypt password hash."""
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


def _verify_legacy_sha256(password: str, password_hash: str) -> bool:
    """Verify a legacy salted SHA-256 password hash."""
    try:
        parts = password_hash.split(":", 1)
        if len(parts) != 2:
            return False
        salt, stored_hash = parts
        pwd_hash = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return secrets.compare_digest(pwd_hash, stored_hash)
    except Exception:
        return False


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password supporting Argon2, Scrypt, and legacy salted SHA-256."""
    if not password_hash:
        return False
    if password_hash.startswith("$argon2"):
        return _verify_argon2(password, password_hash)
    if password_hash.startswith("scrypt:"):
        return _verify_scrypt(password, password_hash)
    return _verify_legacy_sha256(password, password_hash)


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
    except (sqlite3.Error, OSError) as e:
        raise RuntimeError(f"Failed to create user: {str(e)}") from e


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
