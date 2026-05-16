"""User management database operations."""

import hashlib
import logging
import secrets
import sqlite3
from datetime import datetime

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

from aird.sql_identifiers import format_update_by_id_sql

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """Hash a password using Argon2 (Priority 1). Falls back to Scrypt if Argon2 unavailable."""
    if ARGON2_AVAILABLE and PH is not None:
        return PH.hash(password)
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1, dklen=32
    )
    return f"scrypt:{salt}:{key.hex()}"


def _verify_argon2(password: str, password_hash: str) -> bool:
    if not (ARGON2_AVAILABLE and PH is not None):
        return False
    try:
        return PH.verify(password_hash, password)
    except (argon2_exceptions.VerifyMismatchError, Exception):
        return False


def _verify_scrypt(password: str, password_hash: str) -> bool:
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
                "INSERT INTO users (username, password_hash, role, created_at, must_change_password) VALUES (?, ?, ?, ?, 0)",
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
            "must_change_password": False,
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{username}' already exists")
    except (sqlite3.Error, OSError) as e:
        raise RuntimeError(f"Failed to create user: {str(e)}") from e


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    """Get user by username"""
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at, active, last_login, must_change_password FROM users WHERE username = ?",
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
                "must_change_password": bool(row[7]),
            }
        return None
    except Exception:
        return None


def get_all_users(conn: sqlite3.Connection) -> list[dict]:
    """Get all users from the database"""
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, active, last_login, must_change_password FROM users ORDER BY created_at DESC"
        ).fetchall()

        return [
            {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5],
                "must_change_password": bool(row[6]),
            }
            for row in rows
        ]
    except Exception:
        return []


def search_users(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Search users by username (case-insensitive)"""
    try:
        escaped_query = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        rows = conn.execute(
            "SELECT id, username, role, created_at, active, last_login, must_change_password FROM users WHERE username LIKE ? ESCAPE '\\' AND active = 1 ORDER BY username LIMIT 20",
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
                "must_change_password": bool(row[6]),
            }
            for row in rows
        ]
    except Exception:
        return []


def _maybe_user_update_clause(field: str, value) -> tuple[str, ...] | None:
    """Return (sql_fragment, bind_value(s)) if *field* should update a column; else None."""
    valid_fields = frozenset(
        [
            "username",
            "password_hash",
            "role",
            "active",
            "last_login",
            "must_change_password",
        ]
    )
    plain_field_sql = {
        "username": "username = ?",
        "password_hash": "password_hash = ?",
        "role": "role = ?",
        "last_login": "last_login = ?",
        "must_change_password": "must_change_password = ?",
    }

    if field == "password":
        return ("password_hash = ?", hash_password(value)) if value else None
    if field not in valid_fields:
        return None
    if field == "active":
        return ("active = ?", 1 if value else 0)
    if field == "must_change_password":
        return ("must_change_password = ?", 1 if value else 0)
    if field in plain_field_sql:
        return (plain_field_sql[field], value)
    return None


def _append_user_update_fragments(kwargs: dict, updates: list, values: list) -> None:
    """Populate *updates* and *values* from user kw fields (same rules as legacy update loop)."""
    for field, value in kwargs.items():
        clause = _maybe_user_update_clause(field, value)
        if clause is None:
            continue
        updates.append(clause[0])
        values.append(clause[1])


def update_user(conn: sqlite3.Connection, user_id: int, **kwargs) -> bool:
    """Update user information"""
    try:
        updates: list[str] = []
        values: list = []
        _append_user_update_fragments(kwargs, updates, values)

        if not updates:
            return False

        values.append(user_id)
        query = format_update_by_id_sql("users", ", ".join(updates))

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

            user = get_user_by_username(conn, username)
            if user:
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
        update_user(conn, user["id"], last_login=datetime.now().isoformat())
        must_change = bool(user.get("must_change_password"))
        del user["password_hash"]
        user["must_change_password"] = must_change
        return user
    return None
