"""Encrypted per-user plugin tokens (GitLab, OneDrive) in SQLite.

Ciphertext uses the same Fernet key as network-share passwords
(AIRD_SECRETS_KEY, else AIRD_COOKIE_SECRET).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from aird.core.secret_storage import decrypt_secret, encrypt_secret

SECRET_GITLAB = "gitlab"
SECRET_ONEDRIVE = "onedrive"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn(conn: sqlite3.Connection | None) -> sqlite3.Connection | None:
    if conn is not None:
        return conn
    import aird.constants as constants_module

    return getattr(constants_module, "DB_CONN", None)


def get_plugin_secret(
    conn: sqlite3.Connection | None,
    username: str,
    secret_key: str,
) -> str | None:
    db = _conn(conn)
    name = (username or "").strip()
    if db is None or not name or not secret_key:
        return None
    row = db.execute(
        """
        SELECT ciphertext FROM user_plugin_secrets
        WHERE username = ? AND secret_key = ?
        """,
        (name, secret_key),
    ).fetchone()
    if not row:
        return None
    value = decrypt_secret(row[0] or "").strip()
    return value or None


def set_plugin_secret(
    conn: sqlite3.Connection | None,
    username: str,
    secret_key: str,
    plaintext: str,
) -> None:
    db = _conn(conn)
    name = (username or "").strip()
    if db is None:
        raise RuntimeError("Database not available")
    if not name or not secret_key:
        raise ValueError("username and secret_key are required")
    token = (plaintext or "").strip()
    if not token:
        raise ValueError("token is required")
    db.execute(
        """
        INSERT INTO user_plugin_secrets (username, secret_key, ciphertext, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(username, secret_key) DO UPDATE SET
            ciphertext = excluded.ciphertext,
            updated_at = excluded.updated_at
        """,
        (name, secret_key, encrypt_secret(token), _now()),
    )
    db.commit()


def delete_plugin_secret(
    conn: sqlite3.Connection | None,
    username: str,
    secret_key: str,
) -> None:
    db = _conn(conn)
    name = (username or "").strip()
    if db is None or not name or not secret_key:
        return
    db.execute(
        "DELETE FROM user_plugin_secrets WHERE username = ? AND secret_key = ?",
        (name, secret_key),
    )
    db.commit()


def plugin_secret_configured(
    conn: sqlite3.Connection | None,
    username: str,
    secret_key: str,
) -> bool:
    return bool(get_plugin_secret(conn, username, secret_key))
