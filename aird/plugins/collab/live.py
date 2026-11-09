"""Tell chat members when a shared poll, list, or meeting changes."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def notify_changed(conn: sqlite3.Connection, kind: str, artifact_id: str) -> None:
    try:
        _fanout(conn, kind, artifact_id)
    except Exception:
        logger.debug("collab fanout failed", exc_info=True)


_TABLE = {
    "poll": ("polls", "poll_participants", "poll_id", "question", "/polls"),
    "list": ("collab_lists", "list_participants", "list_id", "title", "/lists"),
    "meet": ("meets", "meet_participants", "meet_id", "title", "/meet"),
}


def audience_names(conn: sqlite3.Connection, kind: str, artifact_id: str) -> set[str]:
    """Organizer, explicit participants, chat members, and tag members."""
    spec = _TABLE.get(str(kind or "").strip().lower())
    artifact_id = str(artifact_id or "").strip()
    if not spec or not artifact_id:
        return set()
    table, part, col, _title, _url = spec
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (artifact_id,)).fetchone()
    except sqlite3.Error:
        return set()
    if not row:
        return set()
    names = {str(row["organizer"] or "").strip()}
    try:
        for person in conn.execute(
            f"SELECT username FROM {part} WHERE {col} = ?", (artifact_id,)
        ).fetchall():
            name = str(person[0] or "").strip()
            if name:
                names.add(name)
    except sqlite3.Error:
        pass
    try:
        refs = conn.execute(
            "SELECT conversation_id, created_by FROM collab_conv_refs WHERE kind = ? AND artifact_id = ?",
            (kind, artifact_id),
        ).fetchall()
    except sqlite3.Error:
        refs = []
    from aird.plugins.chat import db as chat_db

    for ref in refs:
        cid = str(ref["conversation_id"] or "")
        actor = str(ref["created_by"] or "")
        if not cid or not actor:
            continue
        try:
            for member in chat_db.members_of(actor, cid):
                name = str(member.get("username") or "").strip()
                if name:
                    names.add(name)
        except Exception:
            continue
    try:
        tagged = conn.execute(
            """
            SELECT m.username
            FROM user_tags ut
            JOIN user_tag_members m ON m.tag_id = ut.id
            JOIN user_tag_links l ON l.tag_id = ut.id
            WHERE ut.owner_username = ?
              AND l.kind = ?
              AND l.resource_key = ?
              AND ut.access != 'none'
            """,
            (str(row["organizer"] or ""), kind, artifact_id),
        ).fetchall()
        for person in tagged:
            name = str(person[0] or "").strip()
            if name:
                names.add(name)
    except sqlite3.Error:
        pass
    names.discard("")
    return names


def notify_event(
    conn: sqlite3.Connection,
    *,
    kind: str,
    artifact_id: str,
    actor: str,
    preview: str,
    recipients: set[str] | None = None,
) -> None:
    try:
        _notify_event(
            conn,
            kind=kind,
            artifact_id=artifact_id,
            actor=actor,
            preview=preview,
            recipients=recipients,
        )
    except Exception:
        logger.debug("collab notify failed", exc_info=True)


def _place(conn: sqlite3.Connection, kind: str, artifact_id: str) -> tuple[str, str, str]:
    spec = _TABLE.get(kind)
    if not spec:
        return "Aird", "/chat", ""
    table, _part, _col, title_col, url = spec
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (artifact_id,)).fetchone()
    title = str(row[title_col] or kind) if row else kind
    if kind == "meet" and row is not None:
        slug = str(row["slug"] or "") if "slug" in row.keys() else ""
        if slug:
            url = f"/meet/{slug}"
    conv = ""
    try:
        ref = conn.execute(
            """SELECT conversation_id FROM collab_conv_refs
               WHERE kind = ? AND artifact_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (kind, artifact_id),
        ).fetchone()
        if ref:
            conv = str(ref[0] or "")
    except sqlite3.Error:
        conv = ""
    return title, url, conv


def _notify_event(
    conn: sqlite3.Connection,
    *,
    kind: str,
    artifact_id: str,
    actor: str,
    preview: str,
    recipients: set[str] | None,
) -> None:
    kind = str(kind or "").strip().lower()
    artifact_id = str(artifact_id or "").strip()
    actor = str(actor or "").strip()
    if not kind or not artifact_id or not preview:
        return
    names = set(recipients) if recipients is not None else audience_names(conn, kind, artifact_id)
    names = {n.strip() for n in names if n and n.strip() and n.strip().lower() != actor.lower()}
    if not names:
        return
    title, url, conv = _place(conn, kind, artifact_id)
    text = preview if title.lower() in preview.lower() else f"{preview} · {title}"
    from aird.notifications.engine import deliver
    from aird.plugins.chat import db as chat_db

    for name in names:
        uid = chat_db.resolve_user_id(conn, name)
        if not uid:
            continue
        deliver(
            username=name,
            user_id=int(uid),
            kind="collab",
            socket_payload={
                "type": "chat_notify",
                "kind": "collab",
                "conversation_id": conv,
                "sender": actor or title,
                "preview": text,
                "title": title,
                "url": url,
                "collab_kind": kind,
                "collab_id": artifact_id,
            },
            push_payload={
                "title": title,
                "body": text,
                "sender": actor or title,
                "preview": text,
                "conversation_id": conv,
                "url": url,
                "tag": f"aird-{kind}-{artifact_id}",
                "kind": "collab",
            },
        )


def pin_for_chat(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    kind: str,
    artifact_id: str,
    username: str,
) -> None:
    try:
        from aird.plugins.chat import db as chat_db
        from aird.plugins.collab.refs import link_ref

        members: list[str] = []
        try:
            members = [
                m.get("username")
                for m in chat_db.members_of(username, conversation_id)
                if m.get("username")
            ]
        except Exception:
            members = []
        link_ref(
            conn,
            conversation_id=conversation_id,
            kind=kind,
            username=username,
            artifact_id=artifact_id,
            member_names=members,
        )
    except Exception:
        logger.debug("collab pin failed", exc_info=True)
    notify_changed(conn, kind, artifact_id)


def _fanout(conn: sqlite3.Connection, kind: str, artifact_id: str) -> None:
    """Push a live refresh to everyone who can be looking at this list, poll, or meeting."""
    kind = str(kind or "").strip().lower()
    artifact_id = str(artifact_id or "").strip()
    spec = _TABLE.get(kind)
    if not spec or not artifact_id:
        return
    table = spec[0]
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (artifact_id,)).fetchone()
    except sqlite3.Error:
        return
    if not row:
        return
    from aird.plugins.collab.acl import VISIBILITY_PUBLIC, normalize_visibility
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.service import get_chat_hub

    hub = get_chat_hub()
    public = normalize_visibility(row["visibility"]) == VISIBILITY_PUBLIC
    if public:
        user_ids = set(hub.connected_user_ids())
    else:
        names = {name.casefold() for name in audience_names(conn, kind, artifact_id)}
        user_ids = set()
        for uid in hub.connected_user_ids():
            shown = (hub.username_for(uid) or "").strip().casefold()
            if shown and shown in names:
                user_ids.add(int(uid))
        for name in names:
            try:
                uid = chat_db.resolve_user_id(conn, name)
            except Exception:
                uid = None
            if uid:
                user_ids.add(int(uid))
    conversations: list[str] = []
    try:
        refs = conn.execute(
            "SELECT conversation_id FROM collab_conv_refs WHERE kind = ? AND artifact_id = ?",
            (kind, artifact_id),
        ).fetchall()
        conversations = [str(ref[0] or "") for ref in refs if ref[0]]
    except sqlite3.Error:
        conversations = []
    if not user_ids:
        return
    hub.broadcast_user_ids(
        list(user_ids),
        {
            "type": "collab_update",
            "kind": kind,
            "id": artifact_id,
            "conversation_ids": conversations,
        },
    )
