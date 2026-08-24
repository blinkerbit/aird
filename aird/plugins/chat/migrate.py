"""Copy legacy central chat_* rows into per-user mailboxes once."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from aird.plugins.chat.mailbox import LEGACY_NS, mailbox

logger = logging.getLogger(__name__)


def _legacy_uuid(kind: str, old_id: int) -> str:
    return str(uuid.uuid5(uuid.UUID(LEGACY_NS), f"{kind}:{old_id}"))


def _has_central_chat(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_conversations'"
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _username(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def migrate_user_mailbox(central: sqlite3.Connection, username: str) -> None:
    with mailbox(username) as box:
        flag = box.conn.execute("SELECT value FROM meta WHERE key = 'migrated'").fetchone()
        if flag and flag[0] == "1":
            return
        if not _has_central_chat(central):
            box.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('migrated', '1')")
            box.conn.commit()
            return
        uid_row = central.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not uid_row:
            box.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('migrated', '1')")
            box.conn.commit()
            return
        uid = int(uid_row[0])
        conv_ids = [
            int(r[0])
            for r in central.execute(
                "SELECT conversation_id FROM chat_members WHERE user_id = ?",
                (uid,),
            ).fetchall()
        ]
        for old_cid in conv_ids:
            _migrate_conversation(central, box.conn, username, uid, old_cid)
        box.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('migrated', '1')")
        box.conn.commit()


def _migrate_conversation(
    central: sqlite3.Connection,
    local: sqlite3.Connection,
    username: str,
    uid: int,
    old_cid: int,
) -> None:
    conv_row = central.execute(
        "SELECT created_at, updated_at FROM chat_conversations WHERE id = ?",
        (old_cid,),
    ).fetchone()
    if not conv_row:
        return
    created_at, updated_at = conv_row[0], conv_row[1]
    member_rows = list(
        central.execute(
            "SELECT user_id FROM chat_members WHERE conversation_id = ?",
            (old_cid,),
        ).fetchall()
    )
    members = []
    for (mid,) in member_rows:
        name = _username(central, int(mid))
        if name:
            members.append((name, int(mid)))
    kind = "dm" if len(members) == 2 else "group"
    conv_id = _legacy_uuid("conv", old_cid)
    title = ""
    if kind == "group":
        title = "Group"
    local.execute(
        """
        INSERT OR IGNORE INTO conversations (id, kind, title, muted, created_at, updated_at)
        VALUES (?, ?, ?, 0, ?, ?)
        """,
        (conv_id, kind, title, created_at, updated_at),
    )
    for name, mid in members:
        local.execute(
            """
            INSERT OR IGNORE INTO members (conversation_id, username, user_id, joined_at)
            VALUES (?, ?, ?, ?)
            """,
            (conv_id, name, mid, created_at),
        )
    msgs = central.execute(
        """
        SELECT id, sender_id, msg_type, body, metadata_json, attachment_path, created_at
        FROM chat_messages WHERE conversation_id = ? ORDER BY id
        """,
        (old_cid,),
    ).fetchall()
    for row in msgs:
        old_mid, sender_id, msg_type, body, metadata_json, attachment_path, created_at = row
        sender = _username(central, int(sender_id)) or "unknown"
        meta = {}
        if metadata_json:
            try:
                meta = json.loads(metadata_json)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        if attachment_path and not meta.get("owner_rel"):
            owner_rel = meta.get("sender_rel") if sender == username else attachment_path
            meta.setdefault("owner_username", sender)
            meta.setdefault("owner_rel", owner_rel or attachment_path)
            meta.setdefault("original_name", (owner_rel or attachment_path).split("/")[-1])
            meta["legacy"] = True
        new_mid = _legacy_uuid("msg", int(old_mid))
        local.execute(
            """
            INSERT OR IGNORE INTO messages
                (id, conversation_id, sender_username, sender_id, msg_type, body,
                 metadata_json, reply_to_id, edited_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                new_mid,
                conv_id,
                sender,
                int(sender_id),
                msg_type,
                body or "",
                json.dumps(meta),
                created_at,
            ),
        )
    read = central.execute(
        """
        SELECT last_read_message_id FROM chat_read_state
        WHERE conversation_id = ? AND user_id = ?
        """,
        (old_cid, uid),
    ).fetchone()
    if read and read[0]:
        local.execute(
            """
            INSERT OR IGNORE INTO read_state (conversation_id, last_read_id, last_read_at)
            VALUES (?, ?, ?)
            """,
            (conv_id, _legacy_uuid("msg", int(read[0])), updated_at),
        )
