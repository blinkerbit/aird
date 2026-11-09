"""Conversation shelf: shared polls, lists, and meetings without a chat bubble."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from aird.plugins.collab import lists_db, meet_db, polls_db
from aird.plugins.collab.status import is_active

REFS_SCHEMA = """
CREATE TABLE IF NOT EXISTS collab_conv_refs (
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, kind, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_collab_conv_refs_conv ON collab_conv_refs(conversation_id);
"""

_KIND = {"poll": "polls", "list": "collab_lists", "meet": "meets"}
_PART = {
    "poll": ("poll_participants", "poll_id"),
    "list": ("list_participants", "list_id"),
    "meet": ("meet_participants", "meet_id"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_for(conn: sqlite3.Connection, kind: str, artifact_id: str = "", slug: str = "") -> sqlite3.Row | None:
    table = _KIND.get(kind)
    if not table:
        return None
    if artifact_id:
        return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (artifact_id,)).fetchone()
    if slug:
        return conn.execute(f"SELECT * FROM {table} WHERE lower(slug) = ?", (slug.lower(),)).fetchone()
    return None


def _grant(conn: sqlite3.Connection, kind: str, row: sqlite3.Row, usernames: list[str]) -> None:
    if str(row["visibility"] or "") != "private":
        return
    table, col = _PART[kind]
    for name in usernames:
        if name:
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({col}, username) VALUES (?, ?)",
                (row["id"], name),
            )


def link_ref(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    kind: str,
    username: str,
    artifact_id: str = "",
    slug: str = "",
    member_names: list[str] | None = None,
) -> dict | None:
    row = _row_for(conn, kind, artifact_id, slug)
    if not row:
        return None
    detail = _detail(conn, kind, row["id"], username)
    if not detail:
        return None
    if kind == "list" and not lists_db.user_may_share(conn, row["id"], username):
        return None
    _grant(conn, kind, row, member_names or [])
    conn.execute(
        """INSERT INTO collab_conv_refs (conversation_id, kind, artifact_id, created_by, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(conversation_id, kind, artifact_id) DO NOTHING""",
        (conversation_id, kind, row["id"], username, _now()),
    )
    conn.commit()
    fresh = _detail(conn, kind, row["id"], username) or detail
    return _public(kind, fresh)


def list_refs(conn: sqlite3.Connection, conversation_id: str, username: str) -> list[dict]:
    rows = conn.execute(
        "SELECT kind, artifact_id FROM collab_conv_refs WHERE conversation_id = ? ORDER BY created_at DESC",
        (conversation_id,),
    ).fetchall()
    out = []
    for row in rows:
        detail = _detail(conn, row["kind"], row["artifact_id"], username)
        if detail:
            out.append(_public(row["kind"], detail))
    return out


def suggest(conn: sqlite3.Connection, username: str, kind: str, query: str) -> list[dict]:
    table = _KIND.get(kind)
    if not table:
        return []
    needle = query.lower().strip()
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 300").fetchall()
    hits = []
    for row in rows:
        detail = _detail(conn, kind, row["id"], username)
        if not detail:
            continue
        slug = str(detail.get("slug") or "").lower()
        title = str(detail.get("title") or detail.get("question") or "").lower()
        if needle and needle not in slug and needle not in title:
            continue
        hits.append(_public(kind, detail))
        if len(hits) >= 8:
            break
    return hits


def _detail(conn, kind, artifact_id, username):
    if kind == "poll":
        return polls_db.poll_detail(conn, artifact_id, username)
    if kind == "list":
        return lists_db.list_detail(conn, artifact_id, username)
    if kind == "meet":
        return meet_db.meet_detail(conn, artifact_id, username)
    return None


def _public(kind: str, detail: dict) -> dict:
    title = detail.get("title") or detail.get("question") or kind
    return {
        "kind": kind,
        "id": detail.get("id"),
        "slug": detail.get("slug") or "",
        "title": title,
        "token": _token(kind, detail.get("slug") or ""),
        "visibility": detail.get("visibility"),
        "live": detail.get("live"),
        "active": is_active(kind, detail),
        "may_share": detail.get("may_share", True),
    }


def _token(kind: str, slug: str) -> str:
    if not slug:
        return ""
    if kind == "poll":
        return f"$${slug}"
    if kind == "meet":
        return f"//{slug}"
    return f"#{slug}"
