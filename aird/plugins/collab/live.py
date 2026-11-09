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
    kind = str(kind or "").strip().lower()
    artifact_id = str(artifact_id or "").strip()
    if not kind or not artifact_id:
        return
    try:
        rows = conn.execute(
            "SELECT conversation_id, created_by FROM collab_conv_refs WHERE kind = ? AND artifact_id = ?",
            (kind, artifact_id),
        ).fetchall()
    except sqlite3.Error:
        return
    if not rows:
        return
    from aird.plugins.chat import db as chat_db
    from aird.plugins.chat.service import get_chat_hub

    user_ids: set[int] = set()
    conversations: list[str] = []
    for row in rows:
        cid = str(row["conversation_id"] or "")
        actor = str(row["created_by"] or "")
        if not cid:
            continue
        conversations.append(cid)
        if not actor:
            continue
        try:
            for uid in chat_db.member_user_ids(actor, cid):
                user_ids.add(int(uid))
        except Exception:
            continue
    if not user_ids:
        return
    get_chat_hub().broadcast_user_ids(
        list(user_ids),
        {
            "type": "collab_update",
            "kind": kind,
            "id": artifact_id,
            "conversation_ids": conversations,
        },
    )
