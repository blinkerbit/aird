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


def _users_columns(conn: sqlite3.Connection) -> set[str]:
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    except sqlite3.Error:
        return set()


def _users_have_authentik(conn: sqlite3.Connection) -> bool:
    return "authentik_username" in _users_columns(conn)


def _users_have_display_name(conn: sqlite3.Connection) -> bool:
    return "display_name" in _users_columns(conn)


def effective_display_name(user: dict | None) -> str:
    """Public label for a user: admin-set display name, else login username."""
    if not user:
        return ""
    name = str(user.get("display_name") or "").strip()
    if name:
        return name
    return str(user.get("username") or "").strip()


def _user_select_suffix(conn: sqlite3.Connection) -> str:
    cols = _users_columns(conn)
    parts = []
    if "authentik_username" in cols:
        parts.append("authentik_username")
    if "display_name" in cols:
        parts.append("display_name")
    return (", " + ", ".join(parts)) if parts else ""


def _user_from_row(row, *, cols: set[str]) -> dict:
    user = {
        "id": row[0],
        "username": row[1],
        "password_hash": row[2],
        "role": row[3],
        "created_at": row[4],
        "active": bool(row[5]),
        "last_login": row[6],
        "must_change_password": bool(row[7]),
    }
    idx = 8
    if "authentik_username" in cols:
        user["authentik_username"] = (row[idx] or "") if idx < len(row) else ""
        idx += 1
    if "display_name" in cols:
        user["display_name"] = (row[idx] or "") if idx < len(row) else ""
    return user


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    """Get user by username"""
    try:
        cols = _users_columns(conn)
        sql = (
            "SELECT id, username, password_hash, role, created_at, active, last_login, "
            "must_change_password"
            + _user_select_suffix(conn)
            + " FROM users WHERE username = ?"
        )
        row = conn.execute(sql, (username,)).fetchone()
        if row:
            return _user_from_row(row, cols=cols)
        return None
    except Exception:
        return None


def get_user_by_authentik_username(conn: sqlite3.Connection, username: str) -> dict | None:
    """Get the AIRD account linked to a stable Authentik username."""
    if not username or not _users_have_authentik(conn):
        return None
    try:
        cols = _users_columns(conn)
        sql = (
            "SELECT id, username, password_hash, role, created_at, active, last_login, "
            "must_change_password"
            + _user_select_suffix(conn)
            + " FROM users WHERE authentik_username = ?"
        )
        row = conn.execute(sql, (username,)).fetchone()
        if row:
            return _user_from_row(row, cols=cols)
        return None
    except sqlite3.Error:
        return None


def set_authentik_username(conn: sqlite3.Connection, user_id: int, authentik_username: str) -> None:
    if not _users_have_authentik(conn):
        return
    with conn:
        conn.execute(
            "UPDATE users SET authentik_username = ? WHERE id = ?",
            (authentik_username, user_id),
        )


def display_names_for_usernames(
    conn: sqlite3.Connection, usernames: list[str]
) -> dict[str, str]:
    """Map login username → display label for the given accounts."""
    names = [str(n).strip() for n in usernames if str(n or "").strip()]
    if not names or conn is None:
        return {}
    uniq = list(dict.fromkeys(names))
    has_display = _users_have_display_name(conn)
    q = ",".join("?" * len(uniq))
    sql = (
        "SELECT username, display_name FROM users WHERE username IN (" + q + ")"
        if has_display
        else "SELECT username FROM users WHERE username IN (" + q + ")"
    )
    try:
        rows = conn.execute(sql, uniq).fetchall()
    except sqlite3.Error:
        return {n: n for n in uniq}
    out: dict[str, str] = {}
    for row in rows:
        username = str(row[0])
        if has_display:
            label = str(row[1] or "").strip() or username
        else:
            label = username
        out[username] = label
    for name in uniq:
        out.setdefault(name, name)
    return out


def get_all_users(conn: sqlite3.Connection) -> list[dict]:
    """Get all users from the database"""
    try:
        cols = _users_columns(conn)
        linked = "authentik_username" in cols
        has_display = "display_name" in cols
        extra = []
        if linked:
            extra.append("authentik_username")
        if has_display:
            extra.append("display_name")
        sql = (
            "SELECT id, username, role, created_at, active, last_login, must_change_password"
            + ((", " + ", ".join(extra)) if extra else "")
            + " FROM users ORDER BY created_at DESC"
        )
        rows = conn.execute(sql).fetchall()

        users = []
        for row in rows:
            item = {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5],
                "must_change_password": bool(row[6]),
            }
            idx = 7
            if linked:
                item["authentik_username"] = (row[idx] or "") if idx < len(row) else ""
                idx += 1
            if has_display:
                item["display_name"] = (row[idx] or "") if idx < len(row) else ""
            users.append(item)
        return users
    except Exception:
        return []


def search_users(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Search users by username or display name (case-insensitive)"""
    try:
        escaped_query = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        has_display = _users_have_display_name(conn)
        where = "username LIKE ? ESCAPE '\\'"
        params: list = [f"%{escaped_query}%"]
        if has_display:
            where = (
                "(username LIKE ? ESCAPE '\\' OR display_name LIKE ? ESCAPE '\\')"
            )
            params = [f"%{escaped_query}%", f"%{escaped_query}%"]
        select = (
            "SELECT id, username, role, created_at, active, last_login, must_change_password"
            + (", display_name" if has_display else "")
            + " FROM users WHERE "
            + where
            + " AND active = 1 ORDER BY username LIMIT 20"
        )
        rows = conn.execute(select, params).fetchall()

        out = []
        for row in rows:
            item = {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5],
                "must_change_password": bool(row[6]),
            }
            if has_display:
                item["display_name"] = row[7] or ""
            out.append(item)
        return out
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
            "authentik_username",
            "display_name",
        ]
    )
    plain_field_sql = {
        "username": "username = ?",
        "password_hash": "password_hash = ?",
        "role": "role = ?",
        "last_login": "last_login = ?",
        "must_change_password": "must_change_password = ?",
        "authentik_username": "authentik_username = ?",
        "display_name": "display_name = ?",
    }

    if field == "password":
        return ("password_hash = ?", hash_password(value)) if value else None
    if field not in valid_fields:
        return None
    if field == "active":
        return ("active = ?", 1 if value else 0)
    if field == "must_change_password":
        return ("must_change_password = ?", 1 if value else 0)
    if field == "authentik_username":
        cleaned = (value or "").strip() if isinstance(value, str) else ""
        return ("authentik_username = ?", cleaned or None)
    if field == "display_name":
        cleaned = (value or "").strip() if isinstance(value, str) else ""
        return ("display_name = ?", cleaned or None)
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
