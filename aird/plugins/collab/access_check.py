"""Resolve whether a user may reference an artifact in chat."""

from __future__ import annotations

import sqlite3

from aird.plugins.collab import polls_db, lists_db, meet_db


def user_can_access_artifact(
    conn: sqlite3.Connection,
    *,
    username: str,
    kind: str,
    artifact_id: str,
) -> bool:
    k = str(kind or "").strip().lower()
    aid = str(artifact_id or "").strip()
    if not aid or not username:
        return False
    if k == "poll":
        return polls_db.poll_detail(conn, aid, username) is not None
    if k == "list":
        return lists_db.list_detail(conn, aid, username) is not None
    if k == "meet":
        return meet_db.meet_detail(conn, aid, username) is not None
    return False
