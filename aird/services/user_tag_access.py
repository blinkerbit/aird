"""Resolve read/write access granted via per-user tags."""

from __future__ import annotations

import sqlite3

from aird.db.user_tags import (
    ACCESS_NONE,
    ACCESS_READ,
    ACCESS_WRITE,
    KIND_FILE,
    KIND_LIST,
    KIND_MEET,
    KIND_POLL,
    normalize_rel_path,
)

_LEVEL_RANK = {ACCESS_NONE: 0, ACCESS_READ: 1, ACCESS_WRITE: 2}


def _max_level(a: str, b: str) -> str:
    return a if _LEVEL_RANK.get(a, 0) >= _LEVEL_RANK.get(b, 0) else b


def collab_tag_access(
    conn: sqlite3.Connection | None,
    *,
    viewer: str,
    organizer: str,
    kind: str,
    resource_id: str,
) -> str:
    if conn is None or not viewer or not organizer or not resource_id:
        return ACCESS_NONE
    viewer = viewer.strip()
    org = organizer.strip()
    if viewer == org:
        return ACCESS_WRITE
    try:
        rows = conn.execute(
            """
            SELECT ut.access
            FROM user_tags ut
            JOIN user_tag_members m ON m.tag_id = ut.id
            JOIN user_tag_links l ON l.tag_id = ut.id
            WHERE ut.owner_username = ?
              AND m.username = ?
              AND l.kind = ?
              AND l.resource_key = ?
              AND ut.access != 'none'
            """,
            (org, viewer, kind, resource_id),
        ).fetchall()
    except sqlite3.Error:
        return ACCESS_NONE
    level = ACCESS_NONE
    for row in rows:
        level = _max_level(level, str(row[0] or ACCESS_NONE))
    return level


def file_tag_access(
    conn: sqlite3.Connection | None,
    *,
    viewer: str,
    owner_username: str,
    rel_path: str,
) -> str:
    if conn is None or not viewer or not owner_username:
        return ACCESS_NONE
    viewer = viewer.strip()
    owner = owner_username.strip()
    rel = normalize_rel_path(rel_path)
    if viewer == owner:
        return ACCESS_WRITE
    try:
        rows = conn.execute(
            """
            SELECT ut.access, l.resource_key
            FROM user_tags ut
            JOIN user_tag_members m ON m.tag_id = ut.id
            JOIN user_tag_links l ON l.tag_id = ut.id
            WHERE ut.owner_username = ?
              AND m.username = ?
              AND l.kind = 'file'
              AND ut.access != 'none'
            """,
            (owner, viewer),
        ).fetchall()
    except sqlite3.Error:
        return ACCESS_NONE
    level = ACCESS_NONE
    for access, key in rows:
        pattern = normalize_rel_path(key)
        if not pattern:
            continue
        if rel == pattern or (rel and rel.startswith(pattern + "/")):
            level = _max_level(level, str(access or ACCESS_NONE))
    return level


def poll_tag_access(conn, viewer, organizer, poll_id: str) -> str:
    return collab_tag_access(
        conn, viewer=viewer, organizer=organizer, kind=KIND_POLL, resource_id=poll_id
    )


def list_tag_access(conn, viewer, organizer, list_id: str) -> str:
    return collab_tag_access(
        conn, viewer=viewer, organizer=organizer, kind=KIND_LIST, resource_id=list_id
    )


def meet_tag_access(conn, viewer, organizer, meet_id: str) -> str:
    return collab_tag_access(
        conn, viewer=viewer, organizer=organizer, kind=KIND_MEET, resource_id=meet_id
    )


def collab_may_edit_via_tag(conn, viewer: str, organizer: str, kind: str, resource_id: str) -> bool:
    return collab_tag_access(
        conn,
        viewer=viewer,
        organizer=organizer,
        kind=kind,
        resource_id=resource_id,
    ) == ACCESS_WRITE


def file_may_write_via_tag(conn, viewer: str, owner: str, rel_path: str) -> bool:
    return file_tag_access(conn, viewer=viewer, owner_username=owner, rel_path=rel_path) == ACCESS_WRITE
