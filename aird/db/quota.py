"""User quota database operations."""

import logging
import sqlite3

logger = logging.getLogger(__name__)


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
        logger.debug("get_user_quota failed for %s", username, exc_info=True)
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
        logger.debug("update_user_used_bytes failed for %s", username, exc_info=True)


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
        logger.debug("set_user_quota failed for %s", username, exc_info=True)
