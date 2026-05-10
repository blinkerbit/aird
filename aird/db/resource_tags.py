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


def delete_resource_tag_by_name(conn: sqlite3.Connection, tag: str) -> int:
    """Delete ALL rules for a given tag name. Returns the number of rows deleted."""
    if conn is None or not tag:
        return 0
    try:
        with conn:
            cur = conn.execute("DELETE FROM resource_tags WHERE tag = ?", (tag,))
            return cur.rowcount
    except sqlite3.Error as exc:
        logger.warning("delete_resource_tag_by_name failed: %s", exc)
        return 0


def update_resource_tag(
    conn: sqlite3.Connection,
    tag_id: int,
    *,
    tag: str | None = None,
    glob_pattern: str | None = None,
    priority: int | None = None,
) -> bool:
    """Update one or more fields on an existing tag rule. Returns True on success."""
    if conn is None or tag_id is None:
        return False
    updates, values = [], []
    if tag is not None:
        updates.append("tag = ?")
        values.append(tag)
    if glob_pattern is not None:
        updates.append("glob_pattern = ?")
        values.append(glob_pattern)
    if priority is not None:
        updates.append("priority = ?")
        values.append(int(priority))
    if not updates:
        return False
    values.append(int(tag_id))
    try:
        with conn:
            cur = conn.execute(
                f"UPDATE resource_tags SET {', '.join(updates)} WHERE id = ?",  # noqa: S608
                values,
            )
            return cur.rowcount > 0
    except sqlite3.Error as exc:
        logger.warning("update_resource_tag failed: %s", exc)
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
