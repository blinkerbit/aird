"""Helpers wiring user tags to collab artifacts and tagged browse pages."""

from __future__ import annotations

import os
import sqlite3

from aird.core.security import join_path, is_within_root
from aird.core.file_operations import get_files_by_tag_patterns
from aird.db import user_tags as user_tags_db
from aird.db.resource_tags import list_resource_tags
from aird.db.tag_colors import get_tag_colors_map
from aird.db.user_tags import (
    KIND_FILE,
    KIND_LIST,
    KIND_MEET,
    KIND_POLL,
    add_link,
    get_tag,
    normalize_rel_path,
)


def tag_ids_for_resource(
    conn: sqlite3.Connection | None,
    owner_username: str,
    kind: str,
    resource_key: str,
) -> list[int]:
    if conn is None or not owner_username or not resource_key:
        return []
    try:
        rows = conn.execute(
            """
            SELECT l.tag_id
            FROM user_tag_links l
            JOIN user_tags ut ON ut.id = l.tag_id
            WHERE ut.owner_username = ? AND l.kind = ? AND l.resource_key = ?
            ORDER BY l.tag_id
            """,
            (owner_username, kind, resource_key),
        ).fetchall()
        return [int(r[0]) for r in rows]
    except sqlite3.Error:
        return []


def replace_artifact_tag_links(
    conn: sqlite3.Connection | None,
    *,
    owner_username: str,
    kind: str,
    resource_key: str,
    tag_ids: list[int] | None,
) -> None:
    if conn is None or tag_ids is None or not owner_username or not resource_key:
        return
    kind = str(kind or "").strip().lower()
    try:
        with conn:
            conn.execute(
                """
                DELETE FROM user_tag_links
                WHERE kind = ? AND resource_key = ?
                  AND tag_id IN (SELECT id FROM user_tags WHERE owner_username = ?)
                """,
                (kind, resource_key, owner_username),
            )
            seen: set[int] = set()
            for raw in tag_ids:
                try:
                    tid = int(raw)
                except (TypeError, ValueError):
                    continue
                if tid in seen:
                    continue
                seen.add(tid)
                add_link(
                    conn,
                    tid,
                    owner_username=owner_username,
                    kind=kind,
                    resource_key=resource_key,
                )
    except sqlite3.Error:
        return


def get_user_tag_by_name(conn: sqlite3.Connection, owner_username: str, name: str) -> dict | None:
    if not owner_username or not name:
        return None
    try:
        row = conn.execute(
            "SELECT id FROM user_tags WHERE owner_username = ? AND name = ?",
            (owner_username, name.strip()),
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return get_tag(conn, int(row[0]))


def _append_path(entries: list[dict], rel: str) -> None:
    rel = normalize_rel_path(rel)
    if not rel:
        return
    is_dir = rel.endswith("/")
    norm = rel.rstrip("/")
    name = os.path.basename(norm) or norm
    for existing in entries:
        if existing.get("path") == rel:
            return
    entries.append({"path": rel, "name": name, "is_dir": is_dir})


def list_user_tag_file_entries(
    conn: sqlite3.Connection,
    *,
    owner_username: str,
    tag_name: str,
    data_root: str,
) -> tuple[list[dict], list[str], dict | None]:
    """Return (file rows, linked path keys, tag row) for the owner's tag name."""
    tag = get_user_tag_by_name(conn, owner_username, tag_name)
    if not tag:
        return [], [], None
    root = os.path.abspath(data_root)
    try:
        rows = conn.execute(
            """
            SELECT resource_key FROM user_tag_links
            WHERE tag_id = ? AND kind = ?
            ORDER BY resource_key
            """,
            (int(tag["id"]), KIND_FILE),
        ).fetchall()
    except sqlite3.Error:
        return [], [], tag
    patterns = [normalize_rel_path(str(r[0])) for r in rows if str(r[0] or "").strip()]
    entries: list[dict] = []
    for key in patterns:
        if not key:
            continue
        abspath = join_path(root, key)
        if not is_within_root(abspath, root) or not os.path.exists(abspath):
            continue
        if os.path.isfile(abspath):
            _append_path(entries, key)
            continue
        if os.path.isdir(abspath):
            _append_path(entries, key + "/")
            for dirpath, _dirs, files in os.walk(abspath):
                rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
                for fname in files:
                    rel_file = normalize_rel_path(f"{rel_dir}/{fname}")
                    _append_path(entries, rel_file)
    entries.sort(key=lambda e: e.get("path") or "")
    return entries, patterns, tag


def _count_entries(entries: list[dict]) -> tuple[int, int]:
    file_count = 0
    folder_count = 0
    for entry in entries:
        if entry.get("is_dir"):
            folder_count += 1
        else:
            file_count += 1
    return file_count, folder_count


def _row_val(row, key: str, index: int):
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def list_user_tag_collab_items(
    conn: sqlite3.Connection,
    *,
    tag_id: int,
) -> dict[str, list[dict]]:
    """Polls / lists / meets linked to a user tag."""
    out: dict[str, list[dict]] = {"polls": [], "lists": [], "meets": []}
    try:
        rows = conn.execute(
            """
            SELECT kind, resource_key FROM user_tag_links
            WHERE tag_id = ? AND kind IN (?, ?, ?)
            ORDER BY kind, resource_key
            """,
            (int(tag_id), KIND_POLL, KIND_LIST, KIND_MEET),
        ).fetchall()
    except sqlite3.Error:
        return out

    for row in rows:
        kind = str(_row_val(row, "kind", 0) or "").strip().lower()
        rid = str(_row_val(row, "resource_key", 1) or "").strip()
        if not rid:
            continue
        if kind == KIND_POLL:
            title, href = _poll_label(conn, rid)
            out["polls"].append({"id": rid, "title": title, "href": href, "kind": "poll"})
        elif kind == KIND_LIST:
            title, href = _list_label(conn, rid)
            out["lists"].append({"id": rid, "title": title, "href": href, "kind": "list"})
        elif kind == KIND_MEET:
            title, href = _meet_label(conn, rid)
            out["meets"].append({"id": rid, "title": title, "href": href, "kind": "meet"})
    return out


def _poll_label(conn: sqlite3.Connection, poll_id: str) -> tuple[str, str]:
    href = f"/polls#{poll_id}"
    try:
        row = conn.execute(
            "SELECT question FROM polls WHERE id = ?", (poll_id,)
        ).fetchone()
    except sqlite3.Error:
        return poll_id, href
    if not row:
        return f"Poll {poll_id[:8]}…", href
    return str(_row_val(row, "question", 0) or poll_id), href


def _list_label(conn: sqlite3.Connection, list_id: str) -> tuple[str, str]:
    href = f"/lists#{list_id}"
    try:
        row = conn.execute(
            "SELECT title FROM collab_lists WHERE id = ?", (list_id,)
        ).fetchone()
    except sqlite3.Error:
        return list_id, href
    if not row:
        return f"List {list_id[:8]}…", href
    return str(_row_val(row, "title", 0) or list_id), href


def _meet_label(conn: sqlite3.Connection, meet_id: str) -> tuple[str, str]:
    try:
        row = conn.execute(
            "SELECT title, slug FROM meets WHERE id = ?", (meet_id,)
        ).fetchone()
    except sqlite3.Error:
        return meet_id, "/meet"
    if not row:
        return f"Meeting {meet_id[:8]}…", "/meet"
    title = str(_row_val(row, "title", 0) or meet_id)
    slug = str(_row_val(row, "slug", 1) or "").strip()
    href = f"/meet/{slug}" if slug else f"/meet#{meet_id}"
    return title, href


def build_tagged_page_context(
    conn: sqlite3.Connection | None,
    *,
    username: str,
    tag_name: str,
    data_root: str,
) -> dict:
    """Shared context for /tagged/{name} (user tag first, then ABAC globs)."""
    tag_source = "none"
    user_tag_id = None
    user_tag: dict | None = None
    patterns: list[str] = []
    entries: list[dict] = []
    file_count = 0
    folder_count = 0
    polls: list[dict] = []
    lists: list[dict] = []
    meets: list[dict] = []

    if conn and username:
        entries, patterns, user_tag = list_user_tag_file_entries(
            conn, owner_username=username, tag_name=tag_name, data_root=data_root
        )
        if user_tag:
            tag_source = "user"
            user_tag_id = int(user_tag["id"])
            file_count, folder_count = _count_entries(entries)
            collab = list_user_tag_collab_items(conn, tag_id=user_tag_id)
            polls = collab["polls"]
            lists = collab["lists"]
            meets = collab["meets"]

    if tag_source != "user" and conn:
        rules = list_resource_tags(conn)
        patterns = [r["glob_pattern"] for r in rules if r.get("tag") == tag_name]
        if patterns:
            tag_source = "abac"
            entries = []
            for path in get_files_by_tag_patterns(patterns, data_root):
                is_dir = path.endswith("/")
                if is_dir:
                    folder_count += 1
                    name = path.rstrip("/")
                else:
                    file_count += 1
                    name = path
                entries.append({"path": path, "name": name, "is_dir": is_dir})

    all_tags: set[str] = set()
    tag_colors: dict[str, str] = {}
    if conn and username:
        for t in user_tags_db.list_tags_for_owner(conn, username):
            n = str(t.get("name") or "")
            if n:
                all_tags.add(n)
            c = t.get("color")
            if n and c:
                tag_colors[n] = str(c)
    if conn:
        for r in list_resource_tags(conn):
            n = str(r.get("tag") or "")
            if n:
                all_tags.add(n)
        for k, v in (get_tag_colors_map(conn) or {}).items():
            tag_colors.setdefault(str(k), str(v))
    if user_tag and user_tag.get("color"):
        tag_colors[tag_name] = str(user_tag["color"])

    can_edit_tag_color = bool(user_tag and str(user_tag.get("owner_username") or "") == username)
    total_items = file_count + folder_count + len(polls) + len(lists) + len(meets)

    return {
        "tag_name": tag_name,
        "tag_source": tag_source,
        "user_tag_id": user_tag_id,
        "patterns": patterns,
        "entries": entries,
        "file_count": file_count,
        "folder_count": folder_count,
        "polls": polls,
        "lists": lists,
        "meets": meets,
        "poll_count": len(polls),
        "list_count": len(lists),
        "meet_count": len(meets),
        "total_items": total_items,
        "all_tags": sorted(all_tags),
        "tag_colors": tag_colors,
        "can_edit_tag_color": can_edit_tag_color,
    }


def tags_for_resource_display(
    conn: sqlite3.Connection | None,
    owner_username: str,
    kind: str,
    resource_key: str,
    viewer: str | None,
) -> list[dict]:
    """Tag name, color, and display label for chips on list/poll/meet cards."""
    if conn is None or not owner_username or not resource_key:
        return []
    out: list[dict] = []
    for tid in tag_ids_for_resource(conn, owner_username, kind, resource_key):
        tag = get_tag(conn, tid)
        if not tag:
            continue
        name = str(tag.get("name") or "")
        owner = str(tag.get("owner_username") or owner_username)
        out.append(
            {
                "id": int(tag["id"]),
                "name": name,
                "color": str(tag.get("color") or ""),
                "display_name": user_tags_db.format_tag_display(name, owner, viewer),
            }
        )
    return out


def augment_detail_tag_ids(
    conn: sqlite3.Connection | None,
    detail: dict | None,
    *,
    viewer: str,
    kind: str,
) -> dict | None:
    if not detail or not conn:
        return detail
    org = str(detail.get("organizer") or "")
    if viewer != org:
        return detail
    rid = str(detail.get("id") or "")
    if not rid:
        return detail
    detail = dict(detail)
    detail["tag_ids"] = tag_ids_for_resource(conn, org, kind, rid)
    return detail

