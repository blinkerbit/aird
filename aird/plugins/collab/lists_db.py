"""Lists persistence."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone

from aird.plugins.collab.acl import can_access, normalize_visibility
from aird.services.user_tag_access import ACCESS_READ, ACCESS_WRITE, list_tag_access
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
    updated_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    allow_forward INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS list_items (
    id TEXT PRIMARY KEY,
    list_id TEXT NOT NULL,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    labels_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    due_at TEXT,
    remind_at TEXT,
    reminded_at TEXT,
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
CREATE TABLE IF NOT EXISTS list_groups (
    id TEXT PRIMARY KEY,
    owner_username TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_list_groups_owner ON list_groups(owner_username);
CREATE TABLE IF NOT EXISTS list_group_items (
    owner_username TEXT NOT NULL,
    list_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    UNIQUE(owner_username, list_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_stamp(value) -> str | None:
    """Empty clears the time. A bad value raises ValueError."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(raw)
    except ValueError as err:
        raise ValueError("Invalid time") from err
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat()


def _note(value) -> str:
    return str(value or "").strip()[:4000]


def _new_id() -> str:
    return secrets.token_urlsafe(12)


def _participants(conn: sqlite3.Connection, list_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT username FROM list_participants WHERE list_id = ?", (list_id,)
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
    level = list_tag_access(conn, username, row["organizer"], row["id"])
    return level in (ACCESS_READ, ACCESS_WRITE)


def user_may_share(conn: sqlite3.Connection, list_id: str, username: str) -> bool:
    """Author can always pass a list on. Others only after the author enables forwarding."""
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return False
    if row["organizer"] == username:
        return True
    if "allow_forward" not in row.keys():
        return False
    return bool(row["allow_forward"])


def _user_may_edit_items(conn: sqlite3.Connection, row: sqlite3.Row, username: str) -> bool:
    if row["organizer"] == username:
        return True
    return list_tag_access(conn, username, row["organizer"], row["id"]) == ACCESS_WRITE


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
    filed = conn.execute(
        "SELECT group_id FROM list_group_items WHERE owner_username = ? AND list_id = ?",
        (username, list_id),
    ).fetchone()
    data["group_id"] = str(filed[0]) if filed else ""
    data["may_share"] = user_may_share(conn, list_id, username)
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
    notes: str = "",
) -> dict:
    list_id = _new_id()
    now = _now()
    k = "bullet" if str(kind).strip().lower() == "bullet" else "todo"
    vis = normalize_visibility(visibility)
    conn.execute(
        "INSERT INTO collab_lists (id, organizer, title, kind, visibility, created_at, updated_at, notes) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (list_id, organizer, title.strip(), k, vis, now, now, _note(notes)),
    )
    for i, raw in enumerate(items):
        text = str((raw or {}).get("text") or "").strip()
        if not text:
            continue
        labels = (raw or {}).get("labels") or []
        if not isinstance(labels, list):
            labels = []
        due = parse_stamp((raw or {}).get("due_at")) if (raw or {}).get("due_at") else None
        remind = parse_stamp((raw or {}).get("remind_at")) if (raw or {}).get("remind_at") else None
        conn.execute(
            "INSERT INTO list_items (id, list_id, text, done, sort_order, labels_json, notes, due_at, remind_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                _new_id(),
                list_id,
                text,
                1 if (raw or {}).get("done") else 0,
                i,
                json.dumps(labels),
                _note((raw or {}).get("notes")),
                due,
                remind or due,
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
    notes: str | None = None,
    allow_forward: bool | None = None,
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
    list_notes = row["notes"] if notes is None else _note(notes)
    forwarded = row["allow_forward"] if "allow_forward" in row.keys() else 0
    if allow_forward is not None:
        forwarded = 1 if allow_forward else 0
    conn.execute(
        "UPDATE collab_lists SET title = ?, kind = ?, visibility = ?, notes = ?, allow_forward = ?, updated_at = ? WHERE id = ?",
        (new_title, k, vis, list_notes, forwarded, now, list_id),
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
    *,
    notes: str = "",
    due_at: str | None = None,
    remind_at: str | None = None,
) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_edit_items(conn, row, username):
        return None
    body = str(text or "").strip()
    if not body:
        return None
    now = _now()
    order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM list_items WHERE list_id = ?",
        (list_id,),
    ).fetchone()[0]
    due = parse_stamp(due_at) if due_at else None
    remind = parse_stamp(remind_at) if remind_at else due
    conn.execute(
        "INSERT INTO list_items (id, list_id, text, done, sort_order, labels_json, notes, due_at, remind_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (_new_id(), list_id, body, 0, order, json.dumps(labels or []), _note(notes), due, remind, now, now),
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
    if not row or not _user_may_edit_items(conn, row, username):
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
    notes: str | None = None,
    due_at: str | None = None,
    remind_at: str | None = None,
    due_set: bool = False,
    remind_set: bool = False,
    sort_order: int | None = None,
) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_edit_items(conn, row, username):
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
    if notes is not None:
        conn.execute(
            "UPDATE list_items SET notes = ?, updated_at = ? WHERE id = ?",
            (_note(notes), now, item_id),
        )
    if sort_order is not None:
        conn.execute(
            "UPDATE list_items SET sort_order = ?, updated_at = ? WHERE id = ?",
            (int(sort_order), now, item_id),
        )
    due = parse_stamp(due_at) if due_set else item["due_at"]
    remind = parse_stamp(remind_at) if remind_set else item["remind_at"]
    if due_set and not remind_set and not remind:
        remind = due
    time_changed = (due_set and due != item["due_at"]) or (remind_set and remind != item["remind_at"])
    if due_set or remind_set:
        conn.execute(
            "UPDATE list_items SET due_at = ?, remind_at = ?, updated_at = ? WHERE id = ?",
            (due, remind, now, item_id),
        )
    if done:
        conn.execute("UPDATE list_items SET reminded_at = ? WHERE id = ?", (now, item_id))
    elif time_changed or done is False:
        conn.execute("UPDATE list_items SET reminded_at = NULL WHERE id = ?", (item_id,))
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
        "notes": row["notes"] if "notes" in row.keys() else "",
        "allow_forward": bool(row["allow_forward"]) if "allow_forward" in row.keys() else False,
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
        "notes": row["notes"] if "notes" in row.keys() else "",
        "due_at": row["due_at"] if "due_at" in row.keys() else None,
        "remind_at": row["remind_at"] if "remind_at" in row.keys() else None,
    }


def list_index(conn: sqlite3.Connection, username: str, query: str = "") -> dict:
    from aird.db.user_tag_collab import tags_for_resource_display
    from aird.db.user_tags import KIND_LIST

    needle = query.strip().casefold()
    lists = []
    reminders = []
    tag_catalog: dict[str, dict] = {}
    now = _now()
    for row in conn.execute(
        "SELECT * FROM collab_lists ORDER BY updated_at DESC LIMIT 200"
    ).fetchall():
        if not _user_may_view(conn, row, username):
            continue
        data = _list_row(row)
        items = conn.execute(
            "SELECT * FROM list_items WHERE list_id = ?", (row["id"],)
        ).fetchall()
        done = sum(1 for item in items if item["done"])
        data["progress"] = {"done": done, "total": len(items)}
        data["active"] = is_active("list", {**data, "kind": data["kind"], "progress": data["progress"]})
        open_due = [
            str(item["due_at"])
            for item in items
            if not item["done"] and item["due_at"]
        ]
        data["next_due"] = min(open_due) if open_due else ""
        filed = conn.execute(
            "SELECT group_id FROM list_group_items WHERE owner_username = ? AND list_id = ?",
            (username, row["id"]),
        ).fetchone()
        data["group_id"] = str(filed[0]) if filed else ""
        data["may_share"] = data["organizer"] == username or bool(data.get("allow_forward"))
        data["tags"] = tags_for_resource_display(
            conn, data["organizer"], KIND_LIST, data["id"], username
        )
        for tag in data["tags"]:
            key = str(tag.get("name") or "")
            if key and key not in tag_catalog:
                tag_catalog[key] = {
                    "name": tag["name"],
                    "color": tag.get("color") or "",
                    "display_name": tag.get("display_name") or tag["name"],
                }
        if needle and not _matches(data, items, needle):
            continue
        lists.append(data)
        for item in items:
            if item["done"] or item["reminded_at"]:
                continue
            when = item["remind_at"] or item["due_at"]
            if not when or when < now:
                continue
            reminders.append(
                {
                    "list_id": row["id"],
                    "item_id": item["id"],
                    "text": item["text"],
                    "title": data["title"],
                    "remind_at": when,
                }
            )
    groups = [
        {
            "id": group["id"],
            "name": group["name"],
            "sort_order": group["sort_order"],
        }
        for group in conn.execute(
            "SELECT * FROM list_groups WHERE owner_username = ? ORDER BY sort_order, name",
            (username,),
        ).fetchall()
    ]
    catalog = sorted(tag_catalog.values(), key=lambda t: str(t.get("name") or "").casefold())
    return {"lists": lists, "groups": groups, "reminders": reminders, "tag_catalog": catalog}


def _matches(data: dict, items, needle: str) -> bool:
    hay = [data.get("title") or "", data.get("notes") or ""]
    for item in items:
        hay.append(item["text"] or "")
        hay.append(item["notes"] or "")
    return any(needle in str(part).casefold() for part in hay)


def create_group(conn: sqlite3.Connection, owner: str, name: str) -> dict | None:
    title = name.strip()[:80]
    if not owner or not title:
        return None
    order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM list_groups WHERE owner_username = ?",
        (owner,),
    ).fetchone()[0]
    group_id = _new_id()
    conn.execute(
        "INSERT INTO list_groups (id, owner_username, name, sort_order, created_at) VALUES (?,?,?,?,?)",
        (group_id, owner, title, order, _now()),
    )
    conn.commit()
    return {"id": group_id, "name": title, "sort_order": order}


def update_group(conn: sqlite3.Connection, owner: str, group_id: str, *, name: str | None, sort_order: int | None) -> dict | None:
    row = conn.execute(
        "SELECT * FROM list_groups WHERE id = ? AND owner_username = ?",
        (group_id, owner),
    ).fetchone()
    if not row:
        return None
    title = row["name"] if name is None else name.strip()[:80]
    if not title:
        return None
    order = row["sort_order"] if sort_order is None else int(sort_order)
    conn.execute(
        "UPDATE list_groups SET name = ?, sort_order = ? WHERE id = ?",
        (title, order, group_id),
    )
    conn.commit()
    return {"id": group_id, "name": title, "sort_order": order}


def delete_list(conn: sqlite3.Connection, list_id: str, username: str) -> bool:
    row = conn.execute("SELECT organizer FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or row["organizer"] != username:
        return False
    conn.execute("DELETE FROM list_items WHERE list_id = ?", (list_id,))
    conn.execute("DELETE FROM list_participants WHERE list_id = ?", (list_id,))
    conn.execute("DELETE FROM list_group_items WHERE list_id = ?", (list_id,))
    conn.execute(
        "DELETE FROM collab_conv_refs WHERE kind = 'list' AND artifact_id = ?",
        (list_id,),
    )
    try:
        conn.execute(
            "DELETE FROM user_tag_links WHERE kind = 'list' AND resource_key = ?",
            (list_id,),
        )
    except sqlite3.Error:
        pass
    conn.execute("DELETE FROM collab_lists WHERE id = ?", (list_id,))
    conn.commit()
    return True


def delete_group(conn: sqlite3.Connection, owner: str, group_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM list_groups WHERE id = ? AND owner_username = ?",
        (group_id, owner),
    ).fetchone()
    if not row:
        return False
    conn.execute(
        "DELETE FROM list_group_items WHERE owner_username = ? AND group_id = ?",
        (owner, group_id),
    )
    conn.execute("DELETE FROM list_groups WHERE id = ? AND owner_username = ?", (group_id, owner))
    conn.commit()
    return True


def set_list_group(conn: sqlite3.Connection, list_id: str, username: str, group_id: str) -> bool:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_view(conn, row, username):
        return False
    if not group_id:
        conn.execute(
            "DELETE FROM list_group_items WHERE owner_username = ? AND list_id = ?",
            (username, list_id),
        )
        conn.commit()
        return True
    owned = conn.execute(
        "SELECT 1 FROM list_groups WHERE id = ? AND owner_username = ?",
        (group_id, username),
    ).fetchone()
    if not owned:
        return False
    conn.execute(
        """INSERT INTO list_group_items (owner_username, list_id, group_id) VALUES (?,?,?)
           ON CONFLICT(owner_username, list_id) DO UPDATE SET group_id = excluded.group_id""",
        (username, list_id, group_id),
    )
    conn.commit()
    return True


def reorder_items(conn: sqlite3.Connection, list_id: str, username: str, ids: list[str]) -> dict | None:
    row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
    if not row or not _user_may_edit_items(conn, row, username):
        return None
    for index, item_id in enumerate(ids):
        conn.execute(
            "UPDATE list_items SET sort_order = ? WHERE id = ? AND list_id = ?",
            (index, str(item_id), list_id),
        )
    conn.execute("UPDATE collab_lists SET updated_at = ? WHERE id = ?", (_now(), list_id))
    conn.commit()
    return list_detail(conn, list_id, username)


def claim_due_reminders(conn: sqlite3.Connection) -> list[dict]:
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            """
            SELECT i.id, i.list_id, i.text, l.title
            FROM list_items i
            JOIN collab_lists l ON l.id = i.list_id
            WHERE i.done = 0
              AND (i.reminded_at IS NULL OR i.reminded_at = '')
              AND (
                (i.remind_at IS NOT NULL AND i.remind_at != '' AND i.remind_at <= ?)
                OR (
                  (i.remind_at IS NULL OR i.remind_at = '')
                  AND i.due_at IS NOT NULL AND i.due_at != '' AND i.due_at <= ?
                )
              )
            """,
            (now, now),
        ).fetchall()
        claimed = []
        for row in rows:
            cur = conn.execute(
                """UPDATE list_items SET reminded_at = ?
                   WHERE id = ? AND (reminded_at IS NULL OR reminded_at = '')""",
                (now, row["id"]),
            )
            if cur.rowcount:
                claimed.append(
                    {
                        "list_id": row["list_id"],
                        "item_id": row["id"],
                        "text": row["text"],
                        "title": row["title"],
                    }
                )
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise


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
