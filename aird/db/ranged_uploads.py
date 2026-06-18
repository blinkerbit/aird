"""SQLite persistence for resumable ranged HTTP uploads."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from aird.core.http_range import ByteRange, ranges_from_json, ranges_to_json


def create_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    username: str,
    upload_dir: str,
    filename: str,
    temp_path: str,
    total_size: int,
) -> None:
    conn.execute(
        """
        INSERT INTO ranged_upload_sessions
            (id, username, upload_dir, filename, temp_path, total_size, ranges_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, '[]', ?)
        """,
        (
            session_id,
            username,
            upload_dir,
            filename,
            temp_path,
            total_size,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, username, upload_dir, filename, temp_path, total_size, ranges_json "
        "FROM ranged_upload_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "upload_dir": row[2],
        "filename": row[3],
        "temp_path": row[4],
        "total_size": row[5],
        "ranges": ranges_from_json(json.loads(row[6] or "[]")),
    }


def update_ranges(
    conn: sqlite3.Connection, session_id: str, ranges: list[ByteRange]
) -> None:
    conn.execute(
        "UPDATE ranged_upload_sessions SET ranges_json = ? WHERE id = ?",
        (json.dumps(ranges_to_json(ranges)), session_id),
    )
    conn.commit()


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM ranged_upload_sessions WHERE id = ?", (session_id,))
    conn.commit()
