"""Resource tag (ABAC resource dimension) database operations.

Tags are mapped to file paths via fnmatch-style glob patterns. The same
matching engine used by share allow/avoid lists is reused at evaluation
time (see :func:`aird.core.file_operations.matches_glob_patterns`).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def insert_resource_tag(
    conn: sqlite3.Connection,
    tag: str,
    glob_pattern: str,
    *,
    priority: int = 0,
    created_by: str | None = None,
) -> int | None:
    """Create a tag rule. Returns the new row id, or None on failure/duplicate."""
    if conn is None or not tag or not glob_pattern:
        return None
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO resource_tags (tag, glob_pattern, priority, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (tag, glob_pattern, int(priority), _utcnow_iso(), created_by),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    except sqlite3.Error as exc:
        logger.warning("insert_resource_tag failed: %s", exc)
        return None


def delete_resource_tag(conn: sqlite3.Connection, tag_id: int) -> bool:
    if conn is None or tag_id is None:
        return False
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM resource_tags WHERE id = ?", (int(tag_id),)
            )
            return cur.rowcount > 0
    except sqlite3.Error:
        return False


def list_resource_tags(conn: sqlite3.Connection) -> list[dict]:
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, tag, glob_pattern, priority, created_at, created_by "
            "FROM resource_tags ORDER BY priority DESC, tag, id"
        ).fetchall()
        return [
            {
                "id": r[0],
                "tag": r[1],
                "glob_pattern": r[2],
                "priority": r[3],
                "created_at": r[4],
                "created_by": r[5],
            }
            for r in rows
        ]
    except sqlite3.Error:
        return []
