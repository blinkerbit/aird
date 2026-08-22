"""SQLite CRUD for direct messages."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from aird.db.users import get_user_by_username
from aird.plugins.chat.sanitize import plain_preview


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _user_id(conn: sqlite3.Connection, username: str) -> int | None:
    row = get_user_by_username(conn, username)
    if not row or not row.get("active", True):
        return None
    return int(row["id"])


def find_dm_conversation(
    conn: sqlite3.Connection, user_a_id: int, user_b_id: int
) -> int | None:
    row = conn.execute(
        """
        SELECT c.id
        FROM chat_conversations c
        JOIN chat_members m1 ON m1.conversation_id = c.id AND m1.user_id = ?
        JOIN chat_members m2 ON m2.conversation_id = c.id AND m2.user_id = ?
        WHERE (SELECT COUNT(*) FROM chat_members WHERE conversation_id = c.id) = 2
        LIMIT 1
        """,
        (user_a_id, user_b_id),
    ).fetchone()
    return int(row[0]) if row else None


def create_dm_conversation(
    conn: sqlite3.Connection, user_a_id: int, user_b_id: int
) -> int:
    existing = find_dm_conversation(conn, user_a_id, user_b_id)
    if existing is not None:
        return existing
    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO chat_conversations (created_at, updated_at) VALUES (?, ?)",
        (now, now),
    )
    conv_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO chat_members (conversation_id, user_id) VALUES (?, ?), (?, ?)",
        (conv_id, user_a_id, conv_id, user_b_id),
    )
    conn.commit()
    return conv_id


def user_in_conversation(
    conn: sqlite3.Connection, conversation_id: int, user_id: int
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM chat_members WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, user_id),
    ).fetchone()
    return row is not None


def other_member_id(
    conn: sqlite3.Connection, conversation_id: int, user_id: int
) -> int | None:
    row = conn.execute(
        """
        SELECT user_id FROM chat_members
        WHERE conversation_id = ? AND user_id != ?
        LIMIT 1
        """,
        (conversation_id, user_id),
    ).fetchone()
    return int(row[0]) if row else None


def _username_for_id(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute(
        "SELECT username FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return row[0] if row else None


def _last_message_row(conn: sqlite3.Connection, conversation_id: int):
    return conn.execute(
        """
        SELECT id, sender_id, msg_type, body, attachment_path, created_at
        FROM chat_messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()


def _unread_count(
    conn: sqlite3.Connection, conversation_id: int, user_id: int
) -> int:
    row = conn.execute(
        """
        SELECT last_read_message_id FROM chat_read_state
        WHERE conversation_id = ? AND user_id = ?
        """,
        (conversation_id, user_id),
    ).fetchone()
    last_read = int(row[0]) if row else 0
    row = conn.execute(
        """
        SELECT COUNT(*) FROM chat_messages
        WHERE conversation_id = ? AND id > ? AND sender_id != ?
        """,
        (conversation_id, last_read, user_id),
    ).fetchone()
    return int(row[0]) if row else 0


def list_conversations(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id, c.updated_at, m.user_id
        FROM chat_conversations c
        JOIN chat_members me ON me.conversation_id = c.id AND me.user_id = ?
        JOIN chat_members m ON m.conversation_id = c.id AND m.user_id != ?
        ORDER BY c.updated_at DESC
        """,
        (user_id, user_id),
    ).fetchall()
    out = []
    for conv_id, updated_at, peer_id in rows:
        peer = _username_for_id(conn, peer_id) or "unknown"
        last = _last_message_row(conn, conv_id)
        preview = ""
        last_type = None
        if last:
            _mid, sender_id, msg_type, body, attachment_path, created_at = last
            last_type = msg_type
            if msg_type == "text":
                preview = plain_preview(body or "")
            elif msg_type == "file":
                preview = f"Shared {attachment_path or 'a file'}"
            elif msg_type == "gif":
                preview = "Sent a GIF"
        out.append(
            {
                "id": conv_id,
                "peer_username": peer,
                "peer_id": peer_id,
                "updated_at": updated_at,
                "unread": _unread_count(conn, conv_id, user_id),
                "last_preview": preview,
                "last_type": last_type,
            }
        )
    return out


def list_messages(
    conn: sqlite3.Connection,
    conversation_id: int,
    *,
    before_id: int | None = None,
    after_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    if after_id:
        rows = conn.execute(
            """
            SELECT id, sender_id, msg_type, body, metadata_json, attachment_path, created_at
            FROM chat_messages
            WHERE conversation_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (conversation_id, after_id, limit),
        ).fetchall()
    elif before_id:
        rows = conn.execute(
            """
            SELECT id, sender_id, msg_type, body, metadata_json, attachment_path, created_at
            FROM chat_messages
            WHERE conversation_id = ? AND id < ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, before_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, sender_id, msg_type, body, metadata_json, attachment_path, created_at
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
    messages = []
    for row in reversed(rows):
        meta = {}
        if row[4]:
            try:
                meta = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                meta = {}
        sender = _username_for_id(conn, row[1]) or "unknown"
        messages.append(
            {
                "id": row[0],
                "sender_id": row[1],
                "sender_username": sender,
                "msg_type": row[2],
                "body": row[3] or "",
                "metadata": meta,
                "attachment_path": row[5],
                "created_at": row[6],
            }
        )
    return messages


def insert_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: int,
    sender_id: int,
    msg_type: str,
    body: str | None = None,
    metadata: dict | None = None,
    attachment_path: str | None = None,
) -> dict:
    now = _now_iso()
    meta_json = json.dumps(metadata or {})
    cur = conn.execute(
        """
        INSERT INTO chat_messages
            (conversation_id, sender_id, msg_type, body, metadata_json, attachment_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            sender_id,
            msg_type,
            body,
            meta_json,
            attachment_path,
            now,
        ),
    )
    msg_id = int(cur.lastrowid)
    conn.execute(
        "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    conn.commit()
    sender = _username_for_id(conn, sender_id) or "unknown"
    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "sender_username": sender,
        "msg_type": msg_type,
        "body": body or "",
        "metadata": metadata or {},
        "attachment_path": attachment_path,
        "created_at": now,
    }


def insert_file_share(
    conn: sqlite3.Connection,
    *,
    conversation_id: int,
    message_id: int,
    sender_id: int,
    recipient_id: int,
    relative_path: str,
    original_name: str,
    size_bytes: int,
) -> None:
    conn.execute(
        """
        INSERT INTO chat_file_shares
            (conversation_id, message_id, sender_id, recipient_id,
             relative_path, original_name, size_bytes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            message_id,
            sender_id,
            recipient_id,
            relative_path,
            original_name,
            size_bytes,
            _now_iso(),
        ),
    )
    conn.commit()


def list_shared_with_me(conn: sqlite3.Connection, recipient_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, relative_path, original_name, size_bytes, created_at, sender_id
        FROM chat_file_shares
        WHERE recipient_id = ?
        ORDER BY created_at DESC
        """,
        (recipient_id,),
    ).fetchall()
    out = []
    for row in rows:
        sender = _username_for_id(conn, row[5]) or "unknown"
        out.append(
            {
                "id": row[0],
                "relative_path": row[1],
                "original_name": row[2],
                "size_bytes": row[3],
                "created_at": row[4],
                "sender_username": sender,
            }
        )
    return out


def list_shared_with_me_grouped(conn: sqlite3.Connection, recipient_id: int) -> list[dict]:
    """Group chat shares by sender for the Shares UI."""
    from aird.constants import CHAT_SHARE_FOLDER
    from aird.core.security import sanitize_username_for_folder

    items = list_shared_with_me(conn, recipient_id)
    groups: dict[str, dict] = {}
    for item in items:
        sender = item["sender_username"]
        if sender not in groups:
            safe = sanitize_username_for_folder(sender) or sender
            groups[sender] = {
                "sender_username": sender,
                "folder_path": f"{CHAT_SHARE_FOLDER}/{safe}",
                "files": [],
            }
        groups[sender]["files"].append(item)
    result = list(groups.values())
    result.sort(
        key=lambda g: g["files"][0]["created_at"] if g["files"] else "",
        reverse=True,
    )
    return result


def get_file_share(conn: sqlite3.Connection, share_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT id, relative_path, original_name, recipient_id, sender_id
        FROM chat_file_shares WHERE id = ?
        """,
        (share_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "relative_path": row[1],
        "original_name": row[2],
        "recipient_id": row[3],
        "sender_id": row[4],
        "sender_username": _username_for_id(conn, row[4]) or "unknown",
    }


def delete_file_share(conn: sqlite3.Connection, share_id: int, recipient_id: int) -> dict | None:
    """Remove index row; caller deletes file on disk."""
    share = get_file_share(conn, share_id)
    if share is None or int(share["recipient_id"]) != recipient_id:
        return None
    conn.execute("DELETE FROM chat_file_shares WHERE id = ?", (share_id,))
    conn.commit()
    return share


def mark_read(
    conn: sqlite3.Connection, conversation_id: int, user_id: int, message_id: int
) -> None:
    row = conn.execute(
        """
        SELECT last_read_message_id FROM chat_read_state
        WHERE conversation_id = ? AND user_id = ?
        """,
        (conversation_id, user_id),
    ).fetchone()
    current = int(row[0]) if row else 0
    if message_id <= current:
        return
    conn.execute(
        """
        INSERT INTO chat_read_state (conversation_id, user_id, last_read_message_id)
        VALUES (?, ?, ?)
        ON CONFLICT(conversation_id, user_id) DO UPDATE SET
            last_read_message_id = excluded.last_read_message_id
        """,
        (conversation_id, user_id, message_id),
    )
    conn.commit()


def unread_summary(conn: sqlite3.Connection, user_id: int) -> dict:
    convs = conn.execute(
        "SELECT conversation_id FROM chat_members WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    total = 0
    items = []
    for (conv_id,) in convs:
        count = _unread_count(conn, conv_id, user_id)
        if count:
            total += count
            items.append({"id": conv_id, "count": count})
    return {"total": total, "conversations": items}


def resolve_user_id(conn: sqlite3.Connection, username: str) -> int | None:
    return _user_id(conn, username)


def get_message(conn: sqlite3.Connection, message_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT id, conversation_id, sender_id, msg_type, body, metadata_json,
               attachment_path, created_at
        FROM chat_messages WHERE id = ?
        """,
        (message_id,),
    ).fetchone()
    if not row:
        return None
    meta = {}
    if row[5]:
        try:
            meta = json.loads(row[5])
        except (json.JSONDecodeError, TypeError):
            meta = {}
    sender = _username_for_id(conn, row[2]) or "unknown"
    return {
        "id": row[0],
        "conversation_id": row[1],
        "sender_id": row[2],
        "sender_username": sender,
        "msg_type": row[3],
        "body": row[4] or "",
        "metadata": meta,
        "attachment_path": row[6],
        "created_at": row[7],
    }


def delete_message(conn: sqlite3.Connection, message_id: int, user_id: int) -> dict | None:
    """Delete a message; only the sender may delete. Returns deleted message info."""
    row = conn.execute(
        "SELECT id, conversation_id, sender_id FROM chat_messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    if not row:
        return None
    msg_id, conversation_id, sender_id = int(row[0]), int(row[1]), int(row[2])
    if sender_id != user_id:
        return None
    if not user_in_conversation(conn, conversation_id, user_id):
        return None
    conn.execute("DELETE FROM chat_file_shares WHERE message_id = ?", (msg_id,))
    conn.execute("DELETE FROM chat_messages WHERE id = ?", (msg_id,))
    conn.commit()
    return {"id": msg_id, "conversation_id": conversation_id}
