"""User attribute (ABAC subject dimension) database operations."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def set_user_attribute(
    conn: sqlite3.Connection, username: str, key: str, value: str
) -> bool:
    """Upsert a user attribute. Returns True on success."""
    if conn is None or not username or not key:
        return False
    try:
        with conn:
            conn.execute(
                "INSERT INTO user_attributes (username, key, value, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(username, key) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                (username, key, str(value), _utcnow_iso()),
            )
        return True
    except sqlite3.Error as exc:
        logger.warning("set_user_attribute failed: %s", exc)
        return False


def delete_user_attribute(
    conn: sqlite3.Connection, username: str, key: str
) -> bool:
    if conn is None or not username or not key:
        return False
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM user_attributes WHERE username = ? AND key = ?",
                (username, key),
            )
            return cur.rowcount > 0
    except sqlite3.Error:
        return False


def get_user_attributes(conn: sqlite3.Connection, username: str) -> dict[str, str]:
    """Return all attributes for a user as a flat dict."""
    if conn is None or not username:
        return {}
    try:
        rows = conn.execute(
            "SELECT key, value FROM user_attributes WHERE username = ?",
            (username,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}
    except sqlite3.Error:
        return {}


def list_all_user_attributes(conn: sqlite3.Connection) -> list[dict]:
    """Return every attribute row (admin view)."""
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, username, key, value, updated_at FROM user_attributes "
            "ORDER BY username, key"
        ).fetchall()
        return [
            {
                "id": r[0],
                "username": r[1],
                "key": r[2],
                "value": r[3],
                "updated_at": r[4],
            }
            for r in rows
        ]
    except sqlite3.Error:
        return []
