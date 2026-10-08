"""Admin-assigned host paths virtualized in a user's browse root."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ALL_USERS = "*"


def _row_to_mount(row: sqlite3.Row | tuple) -> dict:
    if isinstance(row, sqlite3.Row):
        return {
            "id": int(row["id"]),
            "username": str(row["username"]),
            "host_path": str(row["host_path"]),
            "mount_name": str(row["mount_name"]),
            "writable": bool(row["writable"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }
    return {
        "id": int(row[0]),
        "username": str(row[1]),
        "host_path": str(row[2]),
        "mount_name": str(row[3]),
        "writable": bool(row[4]),
        "created_by": row[5],
        "created_at": row[6],
    }


def list_all_path_mounts(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT id, username, host_path, mount_name, writable, created_by, created_at "
            "FROM user_path_mounts ORDER BY username, mount_name"
        ).fetchall()
        return [_row_to_mount(r) for r in rows]
    except Exception:
        logger.debug("list_all_path_mounts failed", exc_info=True)
        return []


def list_user_path_mounts(conn: sqlite3.Connection, username: str) -> list[dict]:
    """Mounts visible to *username*. A user-specific name overrides ``*``."""
    if not username:
        return []
    try:
        rows = conn.execute(
            "SELECT id, username, host_path, mount_name, writable, created_by, created_at "
            "FROM user_path_mounts WHERE username = ? OR username = ? "
            "ORDER BY mount_name",
            (username, ALL_USERS),
        ).fetchall()
    except Exception:
        logger.debug("list_user_path_mounts failed", exc_info=True)
        return []
    by_name: dict[str, dict] = {}
    for row in rows:
        mount = _row_to_mount(row)
        name = mount["mount_name"]
        if mount["username"] == username or name not in by_name:
            by_name[name] = mount
    return list(by_name.values())


def insert_path_mount(
    conn: sqlite3.Connection,
    *,
    username: str,
    host_path: str,
    mount_name: str,
    writable: bool = True,
    created_by: str | None = None,
) -> int | None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO user_path_mounts "
                "(username, host_path, mount_name, writable, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    username,
                    host_path,
                    mount_name,
                    1 if writable else 0,
                    created_by,
                    now,
                ),
            )
            return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None
    except Exception:
        logger.warning("insert_path_mount failed", exc_info=True)
        return None


def delete_path_mount(conn: sqlite3.Connection, mount_id: int) -> bool:
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM user_path_mounts WHERE id = ?", (int(mount_id),)
            )
            return cur.rowcount > 0
    except Exception:
        logger.warning("delete_path_mount failed", exc_info=True)
        return False
