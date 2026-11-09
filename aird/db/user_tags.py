"""Per-user tags: groups for labeling and optional shared read/write access."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from aird.utils.tag_display import next_auto_tag_color, normalize_tag_color

logger = logging.getLogger(__name__)


class MemberValidationError(ValueError):
    """A tag member list references a username that does not exist."""


ACCESS_NONE = "none"
ACCESS_READ = "read"
ACCESS_WRITE = "write"
ACCESS_VALUES = frozenset({ACCESS_NONE, ACCESS_READ, ACCESS_WRITE})

KIND_FILE = "file"
KIND_POLL = "poll"
KIND_LIST = "list"
KIND_MEET = "meet"
LINK_KINDS = frozenset({KIND_FILE, KIND_POLL, KIND_LIST, KIND_MEET})


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def normalize_access(raw: str | None) -> str:
    v = str(raw or "").strip().lower()
    return v if v in ACCESS_VALUES else ACCESS_NONE


def normalize_members(
    conn: sqlite3.Connection,
    owner_username: str,
    members: list[str] | None,
) -> list[str] | None:
    if members is None:
        return None
    from aird.db.users import get_user_by_username

    owner = (owner_username or "").strip()
    out: list[str] = []
    for raw in members:
        u = str(raw or "").strip()
        if not u or u == owner:
            continue
        if get_user_by_username(conn, u) is None:
            raise MemberValidationError(f"Unknown user: {u}")
        if u not in out:
            out.append(u)
    return out


def normalize_rel_path(rel: str) -> str:
    return (rel or "").replace("\\", "/").strip().strip("/")


def format_tag_display(name: str, owner_username: str, viewer: str | None) -> str:
    owner = (owner_username or "").strip()
    if not owner or not viewer or viewer == owner:
        return name
    return f"{name} ({owner})"


def list_tags_for_owner(conn: sqlite3.Connection, owner_username: str) -> list[dict]:
    if conn is None or not owner_username:
        return []
    try:
        rows = conn.execute(
            "SELECT id, owner_username, name, access, color, created_at "
            "FROM user_tags WHERE owner_username = ? ORDER BY name",
            (owner_username,),
        ).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for row in rows:
        out.append(_tag_row(conn, row))
    return out


def get_tag(conn: sqlite3.Connection, tag_id: int) -> dict | None:
    if conn is None or tag_id is None:
        return None
    try:
        row = conn.execute(
            "SELECT id, owner_username, name, access, color, created_at "
            "FROM user_tags WHERE id = ?",
            (int(tag_id),),
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return _tag_row(conn, row)


def create_tag(
    conn: sqlite3.Connection,
    *,
    owner_username: str,
    name: str,
    access: str = ACCESS_NONE,
    color: str | None = None,
    members: list[str] | None = None,
) -> dict | None:
    if conn is None or not owner_username or not name:
        return None
    tag_name = name.strip()
    if not tag_name:
        return None
    acc = normalize_access(access)
    hex_color = normalize_tag_color(color) if color else None
    if not hex_color:
        used = [
            r[0]
            for r in conn.execute(
                "SELECT color FROM user_tags WHERE owner_username = ?", (owner_username,)
            ).fetchall()
        ]
        hex_color = next_auto_tag_color(used, tag_name)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO user_tags (owner_username, name, access, color, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (owner_username, tag_name, acc, hex_color, _utcnow_iso()),
            )
            tag_id = int(cur.lastrowid)
            try:
                cleaned = normalize_members(conn, owner_username, members) if members is not None else []
                _replace_members(conn, tag_id, cleaned or [])
            except MemberValidationError:
                return None
        return get_tag(conn, tag_id)
    except sqlite3.IntegrityError:
        return None
    except sqlite3.Error as exc:
        logger.warning("create_tag failed: %s", exc)
        return None


def update_tag(
    conn: sqlite3.Connection,
    tag_id: int,
    *,
    owner_username: str,
    name: str | None = None,
    access: str | None = None,
    color: str | None = None,
    members: list[str] | None = None,
) -> dict | None:
    row = get_tag(conn, tag_id)
    if not row or row["owner_username"] != owner_username:
        return None
    updates: list[str] = []
    values: list = []
    if name is not None:
        n = name.strip()
        if not n:
            return None
        updates.append("name = ?")
        values.append(n)
    if access is not None:
        updates.append("access = ?")
        values.append(normalize_access(access))
    if color is not None:
        hex_color = normalize_tag_color(color)
        if not hex_color:
            return None
        updates.append("color = ?")
        values.append(hex_color)
    try:
        with conn:
            if updates:
                values.append(int(tag_id))
                conn.execute(
                    f"UPDATE user_tags SET {', '.join(updates)} WHERE id = ?",  # noqa: S608
                    values,
                )
            if members is not None:
                try:
                    cleaned = normalize_members(conn, owner_username, members)
                    _replace_members(conn, tag_id, cleaned or [])
                except MemberValidationError:
                    return None
        return get_tag(conn, tag_id)
    except sqlite3.IntegrityError:
        return None
    except sqlite3.Error as exc:
        logger.warning("update_tag failed: %s", exc)
        return None


def delete_tag(conn: sqlite3.Connection, tag_id: int, *, owner_username: str) -> bool:
    row = get_tag(conn, tag_id)
    if not row or row["owner_username"] != owner_username:
        return False
    try:
        with conn:
            cur = conn.execute("DELETE FROM user_tags WHERE id = ?", (int(tag_id),))
            return cur.rowcount > 0
    except sqlite3.Error:
        return False


def add_link(
    conn: sqlite3.Connection,
    tag_id: int,
    *,
    owner_username: str,
    kind: str,
    resource_key: str,
) -> bool:
    tag = get_tag(conn, tag_id)
    if not tag or tag["owner_username"] != owner_username:
        return False
    k = str(kind or "").strip().lower()
    if k not in LINK_KINDS:
        return False
    key = normalize_rel_path(resource_key) if k == KIND_FILE else str(resource_key or "").strip()
    if not key:
        return False
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_tag_links (tag_id, kind, resource_key) VALUES (?,?,?)",
                (int(tag_id), k, key),
            )
        return True
    except sqlite3.Error:
        return False


def remove_link(
    conn: sqlite3.Connection,
    tag_id: int,
    *,
    owner_username: str,
    kind: str,
    resource_key: str,
) -> bool:
    tag = get_tag(conn, tag_id)
    if not tag or tag["owner_username"] != owner_username:
        return False
    k = str(kind or "").strip().lower()
    key = normalize_rel_path(resource_key) if k == KIND_FILE else str(resource_key or "").strip()
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM user_tag_links WHERE tag_id = ? AND kind = ? AND resource_key = ?",
                (int(tag_id), k, key),
            )
            return cur.rowcount > 0
    except sqlite3.Error:
        return False


def tags_for_file(
    conn: sqlite3.Connection,
    owner_username: str,
    rel_path: str,
    *,
    viewer: str | None = None,
) -> list[dict]:
    """Tags linked to a file path (exact or folder ancestor) for display."""
    rel = normalize_rel_path(rel_path)
    if conn is None or not owner_username:
        return []
    try:
        rows = conn.execute(
            """
            SELECT ut.id, ut.owner_username, ut.name, ut.access, ut.color, ut.created_at,
                   l.resource_key
            FROM user_tags ut
            JOIN user_tag_links l ON l.tag_id = ut.id
            WHERE ut.owner_username = ? AND l.kind = 'file'
            """,
            (owner_username,),
        ).fetchall()
    except sqlite3.Error:
        return []
    matched: dict[int, dict] = {}
    for row in rows:
        pattern = normalize_rel_path(row["resource_key"])
        if not pattern:
            continue
        if rel == pattern or (rel and rel.startswith(pattern + "/")):
            tid = int(row["id"])
            if tid not in matched:
                matched[tid] = _tag_row_from_cols(conn, row, viewer)
    return sorted(matched.values(), key=lambda t: t["name"])


def tag_ids_for_file(conn: sqlite3.Connection, owner_username: str, rel_path: str) -> list[int]:
    return [t["id"] for t in tags_for_file(conn, owner_username, rel_path)]


def list_granted_files(conn: sqlite3.Connection, viewer_username: str) -> list[dict]:
    """Files a viewer can reach via someone else's access tags."""
    if conn is None or not viewer_username:
        return []
    try:
        rows = conn.execute(
            """
            SELECT ut.owner_username, ut.name, ut.access, ut.color, ut.id AS tag_id,
                   l.resource_key
            FROM user_tags ut
            JOIN user_tag_members m ON m.tag_id = ut.id
            JOIN user_tag_links l ON l.tag_id = ut.id
            WHERE m.username = ? AND ut.access IN ('read', 'write') AND l.kind = 'file'
            ORDER BY ut.owner_username, l.resource_key
            """,
            (viewer_username,),
        ).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for row in rows:
        out.append(
            {
                "owner_username": row["owner_username"],
                "path": row["resource_key"],
                "browse_path": f"@{row['owner_username']}/{row['resource_key']}",
                "access": row["access"],
                "tag_id": row["tag_id"],
                "tag_name": format_tag_display(
                    row["name"], row["owner_username"], viewer_username
                ),
                "color": row["color"],
            }
        )
    return out


def _replace_members(conn: sqlite3.Connection, tag_id: int, members: list[str]) -> None:
    conn.execute("DELETE FROM user_tag_members WHERE tag_id = ?", (int(tag_id),))
    for raw in members:
        u = str(raw or "").strip()
        if u:
            conn.execute(
                "INSERT OR IGNORE INTO user_tag_members (tag_id, username) VALUES (?,?)",
                (int(tag_id), u),
            )


def _tag_row(conn: sqlite3.Connection, row) -> dict:
    return _tag_row_from_cols(conn, row, None)


def _tag_row_from_cols(conn: sqlite3.Connection, row, viewer: str | None) -> dict:
    tag_id = int(row["id"])
    members = [
        str(r[0])
        for r in conn.execute(
            "SELECT username FROM user_tag_members WHERE tag_id = ? ORDER BY username",
            (tag_id,),
        ).fetchall()
    ]
    owner = row["owner_username"]
    name = row["name"]
    try:
        file_paths = [
            str(r[0])
            for r in conn.execute(
                "SELECT resource_key FROM user_tag_links WHERE tag_id = ? AND kind = ? ORDER BY resource_key",
                (tag_id, KIND_FILE),
            ).fetchall()
        ]
    except sqlite3.Error:
        file_paths = []
    return {
        "id": tag_id,
        "owner_username": owner,
        "name": name,
        "display_name": format_tag_display(name, owner, viewer),
        "access": row["access"],
        "color": row["color"],
        "created_at": row["created_at"],
        "members": members,
        "files": file_paths,
    }
