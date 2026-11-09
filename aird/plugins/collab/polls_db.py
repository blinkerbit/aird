"""Polls persistence."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone

from aird.plugins.collab.acl import VISIBILITY_PUBLIC, can_access, normalize_visibility
from aird.services.user_tag_access import ACCESS_READ, ACCESS_WRITE, poll_tag_access
from aird.plugins.collab.slugs import assign_slug
from aird.plugins.collab.status import is_active

POLLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    id TEXT PRIMARY KEY,
    organizer TEXT NOT NULL,
    question TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public',
    allow_multi INTEGER NOT NULL DEFAULT 0,
    closes_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS poll_options (
    id TEXT PRIMARY KEY,
    poll_id TEXT NOT NULL,
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(poll_id) REFERENCES polls(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id TEXT NOT NULL,
    option_id TEXT NOT NULL,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(poll_id, username, option_id)
);
CREATE INDEX IF NOT EXISTS idx_poll_votes_poll ON poll_votes(poll_id);
CREATE TABLE IF NOT EXISTS poll_participants (
    poll_id TEXT NOT NULL,
    username TEXT NOT NULL,
    UNIQUE(poll_id, username)
);
CREATE INDEX IF NOT EXISTS idx_poll_participants_poll ON poll_participants(poll_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_urlsafe(12)


def list_polls_for_user(conn: sqlite3.Connection, username: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM polls ORDER BY updated_at DESC LIMIT 200"
    ).fetchall()
    out = []
    for row in rows:
        if _user_may_view(conn, row, username):
            data = _poll_row(row)
            votes = conn.execute(
                "SELECT COUNT(*) FROM poll_votes WHERE poll_id = ?", (row["id"],)
            ).fetchone()[0]
            data["total_votes"] = int(votes or 0)
            data["active"] = is_active("poll", data)
            out.append(data)
    return out


def get_poll(conn: sqlite3.Connection, poll_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
    if not row:
        return None
    return _poll_row(row)


def _participants(conn: sqlite3.Connection, poll_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT username FROM poll_participants WHERE poll_id = ?", (poll_id,)
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
    level = poll_tag_access(conn, username, row["organizer"], row["id"])
    return level in (ACCESS_READ, ACCESS_WRITE)


def _user_may_vote(conn: sqlite3.Connection, row: sqlite3.Row, username: str) -> bool:
    if row["organizer"] == username:
        return True
    if can_access(
        visibility=row["visibility"],
        organizer=row["organizer"],
        username=username,
        participants=_participants(conn, row["id"]),
    ):
        if normalize_visibility(row["visibility"]) == VISIBILITY_PUBLIC:
            return True
        if username in _participants(conn, row["id"]):
            return True
    return poll_tag_access(conn, username, row["organizer"], row["id"]) == ACCESS_WRITE


def create_poll(
    conn: sqlite3.Connection,
    *,
    organizer: str,
    question: str,
    visibility: str,
    allow_multi: bool,
    options: list[str],
    participants: list[str] | None = None,
    slug: str | None = None,
) -> dict:
    poll_id = _new_id()
    now = _now()
    vis = normalize_visibility(visibility)
    conn.execute(
        "INSERT INTO polls (id, organizer, question, visibility, allow_multi, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (poll_id, organizer, question.strip(), vis, 1 if allow_multi else 0, now, now),
    )
    for i, label in enumerate(options):
        label = str(label or "").strip()
        if not label:
            continue
        conn.execute(
            "INSERT INTO poll_options (id, poll_id, label, sort_order) VALUES (?,?,?,?)",
            (_new_id(), poll_id, label, i),
        )
    if vis == "private":
        names = {organizer}
        for p in participants or []:
            if p and p != organizer:
                names.add(p.strip())
        for p in names:
            conn.execute(
                "INSERT OR IGNORE INTO poll_participants (poll_id, username) VALUES (?,?)",
                (poll_id, p),
            )
    assign_slug(conn, "polls", poll_id, question.strip(), slug)
    conn.commit()
    return get_poll(conn, poll_id) or {}


def poll_detail(conn: sqlite3.Connection, poll_id: str, username: str) -> dict | None:
    poll = get_poll(conn, poll_id)
    if not poll:
        return None
    row = conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return None
    options = conn.execute(
        "SELECT id, label, sort_order FROM poll_options WHERE poll_id = ? ORDER BY sort_order",
        (poll_id,),
    ).fetchall()
    counts: dict[str, int] = {}
    for opt in options:
        counts[opt["id"]] = conn.execute(
            "SELECT COUNT(*) FROM poll_votes WHERE option_id = ?", (opt["id"],)
        ).fetchone()[0]
    my_votes = {
        r[0]
        for r in conn.execute(
            "SELECT option_id FROM poll_votes WHERE poll_id = ? AND username = ?",
            (poll_id, username),
        ).fetchall()
    }
    poll["options"] = [
        {
            "id": o["id"],
            "label": o["label"],
            "votes": counts.get(o["id"], 0),
            "mine": o["id"] in my_votes,
        }
        for o in options
    ]
    poll["participants"] = sorted(_participants(conn, poll_id))
    poll["total_votes"] = sum(counts.values())
    poll["active"] = is_active("poll", poll)
    return poll


def update_poll(
    conn: sqlite3.Connection,
    poll_id: str,
    username: str,
    *,
    question: str | None = None,
    options: list[str] | None = None,
    visibility: str | None = None,
    allow_multi: bool | None = None,
    participants: list[str] | None = None,
    slug: str | None = None,
) -> dict | None:
    """Organizer edit. Changing the question, options, or multi-choice clears every vote."""
    row = conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
    if not row:
        return None
    is_org = row["organizer"] == username
    tag_write = poll_tag_access(conn, username, row["organizer"], poll_id) == ACCESS_WRITE
    if not is_org and not tag_write:
        return None
    if not is_org and (
        visibility is not None or participants is not None or slug is not None
    ):
        return None
    now = _now()
    new_question = row["question"] if question is None else question.strip()
    if not new_question:
        return None
    question_changed = new_question != row["question"]
    multi = bool(row["allow_multi"]) if allow_multi is None else bool(allow_multi)
    multi_changed = multi != bool(row["allow_multi"])
    labels: list[str] | None = None
    options_changed = False
    if options is not None:
        labels = []
        for raw in options:
            label = str(raw or "").strip()
            if label and label not in labels:
                labels.append(label)
        if len(labels) < 2:
            return None
        existing = [
            r[0]
            for r in conn.execute(
                "SELECT label FROM poll_options WHERE poll_id = ? ORDER BY sort_order",
                (poll_id,),
            ).fetchall()
        ]
        options_changed = labels != existing
    reset_votes = question_changed or options_changed or multi_changed
    vis = (
        row["visibility"]
        if not is_org or visibility is None
        else normalize_visibility(visibility)
    )
    conn.execute(
        "UPDATE polls SET question = ?, visibility = ?, allow_multi = ?, updated_at = ? WHERE id = ?",
        (new_question, vis, 1 if multi else 0, now, poll_id),
    )
    if options_changed and labels is not None:
        conn.execute("DELETE FROM poll_options WHERE poll_id = ?", (poll_id,))
        for i, label in enumerate(labels):
            conn.execute(
                "INSERT INTO poll_options (id, poll_id, label, sort_order) VALUES (?,?,?,?)",
                (_new_id(), poll_id, label, i),
            )
    if reset_votes:
        conn.execute("DELETE FROM poll_votes WHERE poll_id = ?", (poll_id,))
    if is_org and (visibility is not None or participants is not None):
        if vis == "private":
            names = {username}
            source = participants if participants is not None else _participants(conn, poll_id)
            for p in source:
                if p and str(p).strip() and str(p).strip() != username:
                    names.add(str(p).strip())
            conn.execute("DELETE FROM poll_participants WHERE poll_id = ?", (poll_id,))
            for p in names:
                conn.execute(
                    "INSERT OR IGNORE INTO poll_participants (poll_id, username) VALUES (?,?)",
                    (poll_id, p),
                )
        else:
            conn.execute("DELETE FROM poll_participants WHERE poll_id = ?", (poll_id,))
    if is_org and slug is not None:
        assign_slug(conn, "polls", poll_id, new_question, slug)
    conn.commit()
    return poll_detail(conn, poll_id, username)


def cast_vote(
    conn: sqlite3.Connection,
    poll_id: str,
    username: str,
    option_ids: list[str],
) -> dict | None:
    row = conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return None
    if not _user_may_vote(conn, row, username):
        return None
    if not is_active("poll", {"closes_at": row["closes_at"]}):
        return None
    allow_multi = bool(row["allow_multi"])
    valid = {
        r[0]
        for r in conn.execute(
            "SELECT id FROM poll_options WHERE poll_id = ?", (poll_id,)
        ).fetchall()
    }
    picks = [o for o in option_ids if o in valid]
    if not picks:
        return None
    if not allow_multi:
        picks = picks[:1]
    conn.execute(
        "DELETE FROM poll_votes WHERE poll_id = ? AND username = ?",
        (poll_id, username),
    )
    now = _now()
    for oid in picks:
        conn.execute(
            "INSERT OR IGNORE INTO poll_votes (poll_id, option_id, username, created_at) VALUES (?,?,?,?)",
            (poll_id, oid, username, now),
        )
    conn.execute("UPDATE polls SET updated_at = ? WHERE id = ?", (now, poll_id))
    conn.commit()
    return poll_detail(conn, poll_id, username)


def delete_poll(conn: sqlite3.Connection, poll_id: str, username: str) -> bool:
    row = conn.execute("SELECT organizer FROM polls WHERE id = ?", (poll_id,)).fetchone()
    if not row or row["organizer"] != username:
        return False
    conn.execute("DELETE FROM poll_votes WHERE poll_id = ?", (poll_id,))
    conn.execute("DELETE FROM poll_options WHERE poll_id = ?", (poll_id,))
    conn.execute("DELETE FROM poll_participants WHERE poll_id = ?", (poll_id,))
    conn.execute(
        "DELETE FROM collab_conv_refs WHERE kind = 'poll' AND artifact_id = ?",
        (poll_id,),
    )
    try:
        conn.execute(
            "DELETE FROM user_tag_links WHERE kind = 'poll' AND resource_key = ?",
            (poll_id,),
        )
    except sqlite3.Error:
        pass
    conn.execute("DELETE FROM polls WHERE id = ?", (poll_id,))
    conn.commit()
    return True


def _poll_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "organizer": row["organizer"],
        "question": row["question"],
        "visibility": row["visibility"],
        "allow_multi": bool(row["allow_multi"]),
        "closes_at": row["closes_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "slug": row["slug"] if "slug" in row.keys() else "",
    }


def poll_snapshot(conn: sqlite3.Connection, poll_id: str, username: str) -> dict:
    detail = poll_detail(conn, poll_id, username)
    if not detail:
        return {}
    return {
        "question": detail["question"],
        "total_votes": detail.get("total_votes", 0),
        "options": len(detail.get("options") or []),
    }
