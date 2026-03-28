"""User favorites database operations."""

import sqlite3
from datetime import datetime, timezone


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
