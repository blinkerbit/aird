"""Meet rooms (48h TTL)."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from aird.plugins.collab.acl import can_access, normalize_visibility
from aird.services.user_tag_access import ACCESS_READ, ACCESS_WRITE, meet_tag_access
from aird.plugins.collab.status import is_active
from aird.plugins.collab.slugs import unique_slug, assign_slug

MEET_TTL_HOURS = 48

MEET_SCHEMA = """
CREATE TABLE IF NOT EXISTS meets (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    organizer TEXT NOT NULL,
    title TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public',
    livekit_room TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ended_at TEXT,
    session_started_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_meets_slug ON meets(slug);
CREATE TABLE IF NOT EXISTS meet_participants (
    meet_id TEXT NOT NULL,
    username TEXT NOT NULL,
    UNIQUE(meet_id, username)
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _new_id() -> str:
    return secrets.token_urlsafe(10)


def _slug() -> str:
    return secrets.token_urlsafe(8).replace("_", "-").replace(".", "-")[:12]


def _participants(conn: sqlite3.Connection, meet_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT username FROM meet_participants WHERE meet_id = ?", (meet_id,)
    ).fetchall()
    return {str(r[0]) for r in rows}


def _user_may_view(conn: sqlite3.Connection, row: sqlite3.Row, username: str) -> bool:
    if can_access(
        visibility=row["visibility"],
        organizer=row["organizer"],
        username=username,
        participants=_participants(conn, row["id"]),
    ):
        return True
    level = meet_tag_access(conn, username, row["organizer"], row["id"])
    return level in (ACCESS_READ, ACCESS_WRITE)


def _is_live(row: sqlite3.Row) -> bool:
    if row["ended_at"]:
        return False
    try:
        exp = datetime.fromisoformat(str(row["expires_at"]))
    except ValueError:
        return False
    return _now() < exp


def list_meets_for_user(conn: sqlite3.Connection, username: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM meets ORDER BY created_at DESC LIMIT 200").fetchall()
    out = []
    for row in rows:
        if _user_may_view(conn, row, username):
            out.append(_meet_row(row))
    return out


def get_meet_by_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM meets WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    return _meet_row(row)


def get_meet(conn: sqlite3.Connection, meet_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
    if not row:
        return None
    return _meet_row(row)


def meet_detail(conn: sqlite3.Connection, meet_id: str, username: str) -> dict | None:
    row = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return None
    data = _meet_row(row)
    data["participants"] = sorted(_participants(conn, meet_id))
    data["live"] = _is_live(row)
    data["active"] = is_active("meet", data)
    return data


def meet_detail_slug(conn: sqlite3.Connection, slug: str, username: str) -> dict | None:
    row = conn.execute("SELECT * FROM meets WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    return meet_detail(conn, row["id"], username)


def create_meet(
    conn: sqlite3.Connection,
    *,
    organizer: str,
    title: str,
    visibility: str,
    participants: list[str] | None = None,
    slug: str | None = None,
) -> dict:
    meet_id = _new_id()
    slug = unique_slug(conn, "meets", title, slug)
    now = _now()
    expires = now + timedelta(hours=MEET_TTL_HOURS)
    vis = normalize_visibility(visibility)
    room = f"aird-meet-{meet_id}"
    conn.execute(
        "INSERT INTO meets (id, slug, organizer, title, visibility, livekit_room, created_at, expires_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (meet_id, slug, organizer, title.strip(), vis, room, _iso(now), _iso(expires)),
    )
    if vis == "private":
        names = {organizer}
        for p in participants or []:
            if p and p != organizer:
                names.add(str(p).strip())
        for p in names:
            conn.execute(
                "INSERT OR IGNORE INTO meet_participants (meet_id, username) VALUES (?,?)",
                (meet_id, p),
            )
    conn.commit()
    return get_meet(conn, meet_id) or {}


def update_meet(
    conn: sqlite3.Connection,
    meet_id: str,
    username: str,
    *,
    title: str | None = None,
    visibility: str | None = None,
    participants: list[str] | None = None,
    slug: str | None = None,
) -> dict | None:
    row = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
    if not row or row["organizer"] != username:
        return None
    new_title = row["title"] if title is None else title.strip()
    if not new_title:
        return None
    vis = row["visibility"] if visibility is None else normalize_visibility(visibility)
    conn.execute(
        "UPDATE meets SET title = ?, visibility = ? WHERE id = ?",
        (new_title, vis, meet_id),
    )
    if visibility is not None or participants is not None:
        if vis == "private":
            names = {username}
            source = participants if participants is not None else _participants(conn, meet_id)
            for p in source:
                if p and str(p).strip() and str(p).strip() != username:
                    names.add(str(p).strip())
            conn.execute("DELETE FROM meet_participants WHERE meet_id = ?", (meet_id,))
            for p in names:
                conn.execute(
                    "INSERT OR IGNORE INTO meet_participants (meet_id, username) VALUES (?,?)",
                    (meet_id, p),
                )
        else:
            conn.execute("DELETE FROM meet_participants WHERE meet_id = ?", (meet_id,))
    if slug is not None:
        assign_slug(conn, "meets", meet_id, new_title, slug)
    conn.commit()
    return meet_detail(conn, meet_id, username)


def end_meet(conn: sqlite3.Connection, meet_id: str, username: str) -> bool:
    row = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
    if not row or row["organizer"] != username:
        return False
    conn.execute(
        "UPDATE meets SET ended_at = ?, session_started_at = NULL WHERE id = ?",
        (_iso(_now()), meet_id),
    )
    conn.commit()
    return True


def claim_session_start(conn: sqlite3.Connection, meet_id: str, username: str) -> bool:
    """True when this join is the first of a session, including after the meeting ended."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
        if not row or not _user_may_view(conn, row, username) or not _user_may_join(conn, row, username):
            conn.rollback()
            return False
        if row["ended_at"]:
            try:
                exp = datetime.fromisoformat(str(row["expires_at"]))
            except ValueError:
                exp = _now()
            if _now() >= exp:
                exp = _now() + timedelta(hours=MEET_TTL_HOURS)
            conn.execute(
                "UPDATE meets SET ended_at = NULL, session_started_at = NULL, expires_at = ? WHERE id = ?",
                (_iso(exp), meet_id),
            )
        elif not _is_live(row):
            conn.rollback()
            return False
        cur = conn.execute(
            """UPDATE meets SET session_started_at = ?
               WHERE id = ? AND (session_started_at IS NULL OR session_started_at = '')""",
            (_iso(_now()), meet_id),
        )
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        conn.rollback()
        raise


def _user_may_join(conn: sqlite3.Connection, row: sqlite3.Row, username: str) -> bool:
    if row["organizer"] == username:
        return True
    if can_access(
        visibility=row["visibility"],
        organizer=row["organizer"],
        username=username,
        participants=_participants(conn, row["id"]),
    ):
        return True
    return meet_tag_access(conn, username, row["organizer"], row["id"]) == ACCESS_WRITE


def meet_join_allowed(conn: sqlite3.Connection, meet_id: str, username: str) -> tuple[bool, str]:
    row = conn.execute("SELECT * FROM meets WHERE id = ?", (meet_id,)).fetchone()
    if not row:
        return False, "Not found"
    if not _user_may_view(conn, row, username):
        return False, "Forbidden"
    if not _user_may_join(conn, row, username):
        return False, "Write access required to join"
    if not _is_live(row):
        return False, "Meeting expired or ended"
    return True, ""


def _meet_row(row: sqlite3.Row) -> dict:
    live = _is_live(row)
    return {
        "id": row["id"],
        "slug": row["slug"],
        "organizer": row["organizer"],
        "title": row["title"],
        "visibility": row["visibility"],
        "livekit_room": row["livekit_room"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "ended_at": row["ended_at"],
        "live": live,
        "join_path": f"/meet/{row['slug']}",
    }


def meet_snapshot(conn: sqlite3.Connection, meet_id: str, username: str) -> dict:
    detail = meet_detail(conn, meet_id, username)
    if not detail:
        return {}
    return {
        "title": detail["title"],
        "live": detail.get("live"),
        "slug": detail.get("slug"),
    }
