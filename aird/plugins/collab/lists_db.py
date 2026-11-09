"""Lists persistence."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone

from aird.plugins.collab.acl import can_access, normalize_visibility
from aird.plugins.collab.slugs import assign_slug
from aird.plugins.collab.status import is_active

LISTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS collab_lists (
    id TEXT PRIMARY KEY,
    organizer TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'todo',
    visibility TEXT NOT NULL DEFAULT 'public',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS list_items (
    id TEXT PRIMARY KEY,
    list_id TEXT NOT NULL,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    labels_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(list_id) REFERENCES collab_lists(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_list_items_list ON list_items(list_id);
CREATE TABLE IF NOT EXISTS list_participants (
    list_id TEXT NOT NULL,
    username TEXT NOT NULL,
    UNIQUE(list_id, username)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_urlsafe(12)


def _participants(conn: sqlite3.Connection, list_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT username FROM list_participants WHERE list_id = ?", (list_id,)
    ).fetchall()
    return {str(r[0]) for r in rows}


def _user_may_view(conn: sqlite3.Connection, row: sqlite3.Row, username: str) -> bool:
    return can_access(
        visibility=row["visibility"],
        organizer=row["organizer"],
        username=username,
        participants=_participants(conn, row["id"]),
    )


def list_lists_for_user(conn: sqlite3.Connection, username: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM collab_lists ORDER BY updated_at DESC LIMIT 200"
    ).fetchall()
    out = []
    for row in rows:
        if _user_may_view(conn, row, username):
            out.append(_list_row(row))
    return out


def get_list(conn: sqlite3.Connection, list_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row:
        return None
    return _list_row(row)


def list_detail(conn: sqlite3.Connection, list_id: str, username: str) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return None
    data = _list_row(row)
    items = conn.execute(
        "SELECT * FROM list_items WHERE list_id = ? ORDER BY sort_order, created_at",
        (list_id,),
    ).fetchall()
    data["items"] = [_item_row(i) for i in items]
    data["participants"] = sorted(_participants(conn, list_id))
    done = sum(1 for it in data["items"] if it.get("done"))
    data["progress"] = {"done": done, "total": len(data["items"])}
    data["active"] = is_active("list", data)
    return data


def create_list(
    conn: sqlite3.Connection,
    *,
    organizer: str,
    title: str,
    kind: str,
    visibility: str,
    items: list[dict],
    participants: list[str] | None = None,
    slug: str | None = None,
) -> dict:
    list_id = _new_id()
    now = _now()
    k = "bullet" if str(kind).strip().lower() == "bullet" else "todo"
    vis = normalize_visibility(visibility)
    conn.execute(
        "INSERT INTO collab_lists (id, organizer, title, kind, visibility, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (list_id, organizer, title.strip(), k, vis, now, now),
    )
    for i, raw in enumerate(items):
        text = str((raw or {}).get("text") or "").strip()
        if not text:
            continue
        labels = (raw or {}).get("labels") or []
        if not isinstance(labels, list):
            labels = []
        conn.execute(
            "INSERT INTO list_items (id, list_id, text, done, sort_order, labels_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                _new_id(),
                list_id,
                text,
                1 if (raw or {}).get("done") else 0,
                i,
                json.dumps(labels),
                now,
                now,
            ),
        )
    if vis == "private":
        names = {organizer}
        for p in participants or []:
            if p and p != organizer:
                names.add(str(p).strip())
        for p in names:
            conn.execute(
                "INSERT OR IGNORE INTO list_participants (list_id, username) VALUES (?,?)",
                (list_id, p),
            )
    assign_slug(conn, "collab_lists", list_id, title.strip(), slug)
    conn.commit()
    return get_list(conn, list_id) or {}


def update_list(
    conn: sqlite3.Connection,
    list_id: str,
    username: str,
    *,
    title: str | None = None,
    kind: str | None = None,
    visibility: str | None = None,
    participants: list[str] | None = None,
    slug: str | None = None,
) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or row["organizer"] != username:
        return None
    new_title = row["title"] if title is None else title.strip()
    if not new_title:
        return None
    k = row["kind"]
    if kind is not None:
        k = "bullet" if str(kind).strip().lower() == "bullet" else "todo"
    vis = row["visibility"] if visibility is None else normalize_visibility(visibility)
    now = _now()
    conn.execute(
        "UPDATE collab_lists SET title = ?, kind = ?, visibility = ?, updated_at = ? WHERE id = ?",
        (new_title, k, vis, now, list_id),
    )
    if visibility is not None or participants is not None:
        if vis == "private":
            names = {username}
            source = participants if participants is not None else _participants(conn, list_id)
            for p in source:
                if p and str(p).strip() and str(p).strip() != username:
                    names.add(str(p).strip())
            conn.execute("DELETE FROM list_participants WHERE list_id = ?", (list_id,))
            for p in names:
                conn.execute(
                    "INSERT OR IGNORE INTO list_participants (list_id, username) VALUES (?,?)",
                    (list_id, p),
                )
        else:
            conn.execute("DELETE FROM list_participants WHERE list_id = ?", (list_id,))
    if slug is not None:
        assign_slug(conn, "collab_lists", list_id, new_title, slug)
    conn.commit()
    return list_detail(conn, list_id, username)


def add_item(
    conn: sqlite3.Connection,
    list_id: str,
    username: str,
    text: str,
    labels: list | None = None,
) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return None
    body = str(text or "").strip()
    if not body:
        return None
    now = _now()
    order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM list_items WHERE list_id = ?",
        (list_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO list_items (id, list_id, text, done, sort_order, labels_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (_new_id(), list_id, body, 0, order, json.dumps(labels or []), now, now),
    )
    conn.execute("UPDATE collab_lists SET updated_at = ? WHERE id = ?", (now, list_id))
    conn.commit()
    return list_detail(conn, list_id, username)


def delete_item(
    conn: sqlite3.Connection,
    list_id: str,
    item_id: str,
    username: str,
) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return None
    conn.execute("DELETE FROM list_items WHERE id = ? AND list_id = ?", (item_id, list_id))
    conn.execute("UPDATE collab_lists SET updated_at = ? WHERE id = ?", (_now(), list_id))
    conn.commit()
    return list_detail(conn, list_id, username)


def patch_item(
    conn: sqlite3.Connection,
    list_id: str,
    item_id: str,
    username: str,
    *,
    done: bool | None = None,
    text: str | None = None,
    labels: list | None = None,
) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return None
    item = conn.execute(
        "SELECT * FROM list_items WHERE id = ? AND list_id = ?", (item_id, list_id)
    ).fetchone()
    if not item:
        return None
    now = _now()
    if done is not None:
        conn.execute(
            "UPDATE list_items SET done = ?, updated_at = ? WHERE id = ?",
            (1 if done else 0, now, item_id),
        )
    if text is not None and text.strip():
        conn.execute(
            "UPDATE list_items SET text = ?, updated_at = ? WHERE id = ?",
            (text.strip(), now, item_id),
        )
    if labels is not None:
        clean = [str(x).strip() for x in labels if str(x).strip()]
        conn.execute(
            "UPDATE list_items SET labels_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(clean), now, item_id),
        )
    conn.execute("UPDATE collab_lists SET updated_at = ? WHERE id = ?", (now, list_id))
    conn.commit()
    return list_detail(conn, list_id, username)


def _list_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "organizer": row["organizer"],
        "title": row["title"],
        "kind": row["kind"],
        "visibility": row["visibility"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "slug": row["slug"] if "slug" in row.keys() else "",
    }


def _item_row(row: sqlite3.Row) -> dict:
    try:
        labels = json.loads(row["labels_json"] or "[]")
    except json.JSONDecodeError:
        labels = []
    return {
        "id": row["id"],
        "text": row["text"],
        "done": bool(row["done"]),
        "sort_order": row["sort_order"],
        "labels": labels,
    }


def list_snapshot(conn: sqlite3.Connection, list_id: str, username: str) -> dict:
    detail = list_detail(conn, list_id, username)
    if not detail:
        return {}
    prog = detail.get("progress") or {}
    return {
        "title": detail["title"],
        "kind": detail["kind"],
        "done": prog.get("done", 0),
        "total": prog.get("total", 0),
    }
