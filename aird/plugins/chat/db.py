"""Mailbox CRUD with dual-write fan-out. Identity still lives in the central users table."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from aird.db.users import get_user_by_username
from aird.plugins.chat.files import attachment_entries, attachment_meta, delete_upload_if_owned
from aird.plugins.chat.mailbox import (
    LEGACY_NS,
    MAX_GROUP_MEMBERS,
    MAX_PINS,
    mailbox,
)
from aird.plugins.chat.e2e import (
    ENCRYPTED_PREVIEW,
    is_e2e_meta,
    parse_e2e_payload,
    parse_wrap,
)
from aird.plugins.chat.sanitize import plain_preview

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"@([A-Za-z0-9_.\-@]+)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def legacy_uuid(kind: str, old_id: int) -> str:
    return str(uuid.uuid5(uuid.UUID(LEGACY_NS), f"{kind}:{old_id}"))


def resolve_user_id(conn: sqlite3.Connection, username: str) -> int | None:
    row = get_user_by_username(conn, username)
    if not row or not row.get("active", True):
        return None
    return int(row["id"])


def _username_for_id(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def user_record(conn: sqlite3.Connection, username: str) -> dict | None:
    row = get_user_by_username(conn, username)
    if not row or not row.get("active", True):
        return None
    return {"id": int(row["id"]), "username": row["username"]}


def _loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _meta_name(meta: dict) -> str:
    names = [str(a.get("original_name") or "") for a in attachment_entries(meta)]
    names = [n for n in names if n]
    if names:
        return " ".join(names)
    return str(meta.get("original_name") or "")


def _fts_upsert(box, message_id: str, body: str, original_name: str) -> None:
    if not box.fts:
        return
    try:
        box.conn.execute("DELETE FROM messages_fts WHERE message_id = ?", (message_id,))
        box.conn.execute(
            "INSERT INTO messages_fts(message_id, body, original_name) VALUES (?, ?, ?)",
            (message_id, body or "", original_name or ""),
        )
    except sqlite3.OperationalError:
        box.fts = False


def _fts_delete(box, message_id: str) -> None:
    if not box.fts:
        return
    try:
        box.conn.execute("DELETE FROM messages_fts WHERE message_id = ?", (message_id,))
    except sqlite3.OperationalError:
        box.fts = False


def maybe_migrate_user(central: sqlite3.Connection | None, username: str) -> None:
    if central is None or not username:
        return
    from aird.plugins.chat.migrate import migrate_user_mailbox

    try:
        migrate_user_mailbox(central, username)
    except Exception:
        logger.debug("chat mailbox migrate failed for %s", username, exc_info=True)


def _members_local(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT username, user_id, joined_at FROM members
        WHERE conversation_id = ? ORDER BY username
        """,
        (conversation_id,),
    ).fetchall()
    return [{"username": r[0], "user_id": int(r[1]), "joined_at": r[2]} for r in rows]


def members_of(username: str, conversation_id: str) -> list[dict]:
    with mailbox(username) as box:
        return _members_local(box.conn, conversation_id)


def user_in_conversation(username: str, conversation_id: str) -> bool:
    with mailbox(username) as box:
        row = box.conn.execute(
            "SELECT 1 FROM members WHERE conversation_id = ? AND username = ?",
            (conversation_id, username),
        ).fetchone()
        return row is not None


def get_conversation(username: str, conversation_id: str) -> dict | None:
    with mailbox(username) as box:
        row = box.conn.execute(
            "SELECT id, kind, title, muted, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        members = _members_local(box.conn, conversation_id)
        return {
            "id": row[0],
            "kind": row[1],
            "title": row[2] or "",
            "muted": bool(row[3]),
            "created_at": row[4],
            "updated_at": row[5],
            "members": members,
        }


def _peer_name(members: list[dict], me: str) -> str | None:
    others = [m["username"] for m in members if m["username"] != me]
    return others[0] if len(others) == 1 else None


def find_dm_conversation(username: str, peer_username: str) -> str | None:
    with mailbox(username) as box:
        row = box.conn.execute(
            """
            SELECT c.id FROM conversations c
            JOIN members me ON me.conversation_id = c.id AND me.username = ?
            JOIN members peer ON peer.conversation_id = c.id AND peer.username = ?
            WHERE c.kind = 'dm'
              AND (SELECT COUNT(*) FROM members WHERE conversation_id = c.id) = 2
            LIMIT 1
            """,
            (username, peer_username),
        ).fetchone()
        return row[0] if row else None


def _write_conversation(box, *, conv_id: str, kind: str, title: str, now: str, members: list[dict], muted: int = 0) -> None:
    box.conn.execute(
        """
        INSERT INTO conversations (id, kind, title, muted, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET title = excluded.title, updated_at = excluded.updated_at
        """,
        (conv_id, kind, title, muted, now, now),
    )
    for m in members:
        box.conn.execute(
            """
            INSERT INTO members (conversation_id, username, user_id, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(conversation_id, username) DO NOTHING
            """,
            (conv_id, m["username"], m["user_id"], m.get("joined_at") or now),
        )


def _fanout_members(members: Iterable[dict], fn: Callable) -> None:
    seen: set[str] = set()
    for m in members:
        name = m.get("username")
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            with mailbox(name) as box:
                fn(box, m)
                box.conn.commit()
        except Exception:
            logger.exception("chat fan-out failed for %s", name)


def create_dm_conversation(central: sqlite3.Connection, user_a_id: int, user_b_id: int) -> str:
    name_a = _username_for_id(central, user_a_id)
    name_b = _username_for_id(central, user_b_id)
    if not name_a or not name_b:
        raise ValueError("Unknown user")
    maybe_migrate_user(central, name_a)
    maybe_migrate_user(central, name_b)
    existing = find_dm_conversation(name_a, name_b)
    if existing:
        return existing
    existing = find_dm_conversation(name_b, name_a)
    if existing:
        return existing
    now = _now_iso()
    conv_id = _new_id()
    members = [
        {"username": name_a, "user_id": user_a_id, "joined_at": now},
        {"username": name_b, "user_id": user_b_id, "joined_at": now},
    ]

    def write(box, _m):
        _write_conversation(box, conv_id=conv_id, kind="dm", title="", now=now, members=members)

    _fanout_members(members, write)
    return conv_id


def create_group_conversation(
    central: sqlite3.Connection,
    *,
    creator_username: str,
    title: str,
    member_usernames: list[str],
) -> str:
    maybe_migrate_user(central, creator_username)
    creator = user_record(central, creator_username)
    if not creator:
        raise ValueError("Unknown creator")
    names = []
    seen = {creator_username}
    names.append(creator)
    for raw in member_usernames:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        rec = user_record(central, name)
        if rec is None:
            continue
        seen.add(name)
        names.append(rec)
        maybe_migrate_user(central, name)
    if len(names) < 2:
        raise ValueError("Group needs at least one other member")
    if len(names) > MAX_GROUP_MEMBERS:
        raise ValueError(f"Group cannot exceed {MAX_GROUP_MEMBERS} members")
    now = _now_iso()
    conv_id = _new_id()
    members = [{"username": n["username"], "user_id": n["id"], "joined_at": now} for n in names]
    clean_title = (title or "").strip()[:80] or "Group"

    def write(box, _m):
        _write_conversation(box, conv_id=conv_id, kind="group", title=clean_title, now=now, members=members)

    _fanout_members(members, write)
    return conv_id


def add_member(central: sqlite3.Connection, *, actor: str, conversation_id: str, username: str) -> dict:
    rec = user_record(central, username)
    if rec is None:
        raise ValueError("User not found")
    conv = get_conversation(actor, conversation_id)
    if conv is None:
        raise ValueError("Conversation not found")
    if conv["kind"] != "group":
        raise ValueError("Can only add members to a group")
    current = conv["members"]
    if any(m["username"] == rec["username"] for m in current):
        return rec
    if len(current) >= MAX_GROUP_MEMBERS:
        raise ValueError(f"Group cannot exceed {MAX_GROUP_MEMBERS} members")
    maybe_migrate_user(central, rec["username"])
    now = _now_iso()
    new_m = {"username": rec["username"], "user_id": rec["id"], "joined_at": now}
    updated = current + [new_m]

    def write(box, _m):
        _write_conversation(
            box,
            conv_id=conversation_id,
            kind="group",
            title=conv["title"],
            now=now,
            members=updated,
        )
        box.conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )

    _fanout_members(updated, write)
    return rec


def remove_member(*, actor: str, conversation_id: str, username: str) -> None:
    conv = get_conversation(actor, conversation_id)
    if conv is None:
        raise ValueError("Conversation not found")
    if conv["kind"] != "group":
        raise ValueError("Can only remove members from a group")
    remaining = [m for m in conv["members"] if m["username"] != username]
    if len(remaining) < 1:
        raise ValueError("Group would be empty")
    now = _now_iso()

    def write(box, m):
        if m["username"] == username:
            box.conn.execute("DELETE FROM members WHERE conversation_id = ? AND username = ?", (conversation_id, username))
            return
        box.conn.execute("DELETE FROM members WHERE conversation_id = ? AND username = ?", (conversation_id, username))
        box.conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

    _fanout_members(conv["members"], write)


def set_muted(username: str, conversation_id: str, muted: bool) -> None:
    with mailbox(username) as box:
        box.conn.execute(
            "UPDATE conversations SET muted = ? WHERE id = ?",
            (1 if muted else 0, conversation_id),
        )
        box.conn.commit()


def is_muted(username: str, conversation_id: str) -> bool:
    conv = get_conversation(username, conversation_id)
    return bool(conv and conv.get("muted"))


def _last_message(conn: sqlite3.Connection, conversation_id: str):
    return conn.execute(
        """
        SELECT id, sender_username, msg_type, body, metadata_json, created_at
        FROM messages WHERE conversation_id = ?
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()


def _unread_count(conn: sqlite3.Connection, conversation_id: str, username: str) -> int:
    row = conn.execute(
        "SELECT last_read_id, last_read_at FROM read_state WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not row:
        r = conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE conversation_id = ? AND sender_username != ?
            """,
            (conversation_id, username),
        ).fetchone()
        return int(r[0]) if r else 0
    last_id, last_at = row[0], row[1]
    r = conn.execute(
        """
        SELECT COUNT(*) FROM messages
        WHERE conversation_id = ? AND sender_username != ?
          AND (created_at > ? OR (created_at = ? AND id > ?))
        """,
        (conversation_id, username, last_at, last_at, last_id),
    ).fetchone()
    return int(r[0]) if r else 0


def list_conversations(central: sqlite3.Connection, username: str) -> list[dict]:
    maybe_migrate_user(central, username)
    with mailbox(username) as box:
        rows = box.conn.execute(
            "SELECT id, kind, title, muted, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        out = []
        for conv_id, kind, title, muted, updated_at in rows:
            members = _members_local(box.conn, conv_id)
            last = _last_message(box.conn, conv_id)
            preview = ""
            last_type = None
            last_e2e = None
            if last:
                last_type = last[2]
                meta = _loads(last[4])
                if is_e2e_meta(meta):
                    preview = ENCRYPTED_PREVIEW
                    last_e2e = meta.get("e2e")
                elif last[2] == "text":
                    preview = plain_preview(last[3] or "")
                else:
                    preview = f"Shared {meta.get('original_name') or 'a file'}"
            peer = _peer_name(members, username) if kind == "dm" else None
            out.append(
                {
                    "id": conv_id,
                    "kind": kind,
                    "title": title or "",
                    "peer_username": peer or (title or "Group"),
                    "members": members,
                    "muted": bool(muted),
                    "updated_at": updated_at,
                    "unread": _unread_count(box.conn, conv_id, username),
                    "last_preview": preview,
                    "last_type": last_type,
                    "last_e2e": last_e2e,
                }
            )
        return out


def _message_created(conn: sqlite3.Connection, message_id: str) -> str | None:
    row = conn.execute("SELECT created_at FROM messages WHERE id = ?", (message_id,)).fetchone()
    return row[0] if row else None


def _row_to_message(row, *, reactions: list[dict] | None = None) -> dict:
    meta = _loads(row[6] if len(row) > 6 else None)
    # row: id, conversation_id, sender_username, sender_id, msg_type, body, metadata_json,
    #      reply_to_id, edited_at, created_at
    msg = {
        "id": row[0],
        "conversation_id": row[1],
        "sender_username": row[2],
        "sender_id": int(row[3]),
        "msg_type": row[4],
        "body": row[5] or "",
        "metadata": meta,
        "attachment_path": None,
        "reply_to_id": row[7],
        "edited_at": row[8],
        "created_at": row[9],
        "reactions": reactions or [],
        "mentions": meta.get("mentions") or [],
        "forwarded_from": meta.get("forwarded_from"),
        "e2e": is_e2e_meta(meta),
    }
    if attachment_entries(meta):
        msg["file_url"] = (
            f"/api/chat/conversations/{msg['conversation_id']}/messages/{msg['id']}/file"
        )
    return msg


def _reactions_for(conn: sqlite3.Connection, message_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT username, emoji FROM reactions WHERE message_id = ?",
        (message_id,),
    ).fetchall()
    return [{"username": r[0], "emoji": r[1]} for r in rows]


def _reactions_map(conn: sqlite3.Connection, ids: list[str]) -> dict[str, list[dict]]:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT message_id, username, emoji FROM reactions WHERE message_id IN ({q})",
        ids,
    ).fetchall()
    out: dict[str, list[dict]] = {i: [] for i in ids}
    for mid, username, emoji in rows:
        out.setdefault(mid, []).append({"username": username, "emoji": emoji})
    return out


def list_messages(
    username: str,
    conversation_id: str,
    *,
    before_id: str | None = None,
    after_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    with mailbox(username) as box:
        params: list[Any]
        if after_id:
            ts = _message_created(box.conn, after_id)
            if ts is None:
                return []
            rows = box.conn.execute(
                """
                SELECT id, conversation_id, sender_username, sender_id, msg_type, body,
                       metadata_json, reply_to_id, edited_at, created_at
                FROM messages
                WHERE conversation_id = ? AND (created_at > ? OR (created_at = ? AND id > ?))
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (conversation_id, ts, ts, after_id, limit),
            ).fetchall()
        elif before_id:
            ts = _message_created(box.conn, before_id)
            if ts is None:
                rows = []
            else:
                rows = box.conn.execute(
                    """
                    SELECT id, conversation_id, sender_username, sender_id, msg_type, body,
                           metadata_json, reply_to_id, edited_at, created_at
                    FROM messages
                    WHERE conversation_id = ? AND (created_at < ? OR (created_at = ? AND id < ?))
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (conversation_id, ts, ts, before_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
        else:
            rows = box.conn.execute(
                """
                SELECT id, conversation_id, sender_username, sender_id, msg_type, body,
                       metadata_json, reply_to_id, edited_at, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
            rows = list(reversed(rows))
        ids = [r[0] for r in rows]
        rmap = _reactions_map(box.conn, ids)
        return [_row_to_message(r, reactions=rmap.get(r[0], [])) for r in rows]


def get_message(username: str, message_id: str) -> dict | None:
    with mailbox(username) as box:
        row = box.conn.execute(
            """
            SELECT id, conversation_id, sender_username, sender_id, msg_type, body,
                   metadata_json, reply_to_id, edited_at, created_at
            FROM messages WHERE id = ?
            """,
            (message_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_message(row, reactions=_reactions_for(box.conn, message_id))


def extract_mentions(body: str, member_usernames: Iterable[str]) -> list[str]:
    allowed = set(member_usernames)
    found = []
    for match in _MENTION_RE.finditer(body or ""):
        name = match.group(1)
        if name in allowed and name not in found:
            found.append(name)
    return found


def insert_message(
    central: sqlite3.Connection,
    *,
    username: str,
    conversation_id: str,
    msg_type: str,
    body: str | None = None,
    metadata: dict | None = None,
    reply_to_id: str | None = None,
    message_id: str | None = None,
) -> dict:
    maybe_migrate_user(central, username)
    sender = user_record(central, username)
    if sender is None:
        raise ValueError("Unknown sender")
    conv = get_conversation(username, conversation_id)
    if conv is None:
        raise ValueError("Conversation not found")
    members = conv["members"]
    now = _now_iso()
    mid = message_id or _new_id()
    meta = dict(metadata or {})
    member_names = [m["username"] for m in members if m["username"] != username]
    if is_e2e_meta(meta):
        meta["e2e"] = parse_e2e_payload(meta.get("e2e"))
        body = ""
        allowed = set(member_names)
        mentions = []
        for name in meta.get("mentions") or []:
            name = str(name or "")
            if name in allowed and name not in mentions:
                mentions.append(name)
        meta["mentions"] = mentions
        fts_body = ""
    else:
        mentions = extract_mentions(body or "", member_names)
        if mentions:
            meta["mentions"] = mentions
        fts_body = body or ""
    meta_json = json.dumps(meta)
    original = _meta_name(meta)

    def write(box, _m):
        box.conn.execute(
            """
            INSERT OR REPLACE INTO messages
                (id, conversation_id, sender_username, sender_id, msg_type, body,
                 metadata_json, reply_to_id, edited_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                mid,
                conversation_id,
                sender["username"],
                sender["id"],
                msg_type,
                body or "",
                meta_json,
                reply_to_id,
                now,
            ),
        )
        box.conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        _fts_upsert(box, mid, fts_body, original)

    _fanout_members(members, write)
    msg = get_message(username, mid)
    assert msg is not None
    msg["mentions"] = mentions
    return msg


def edit_message(
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
    body: str,
    e2e: dict | None = None,
    mentions: list[str] | None = None,
) -> dict | None:
    current = get_message(actor, message_id)
    if current is None or current["conversation_id"] != conversation_id:
        return None
    if current["sender_username"] != actor:
        return None
    members = members_of(actor, conversation_id)
    now = _now_iso()
    meta = dict(current.get("metadata") or {})
    member_names = [m["username"] for m in members if m["username"] != actor]
    if e2e is not None:
        meta["e2e"] = parse_e2e_payload(e2e)
        body = ""
        allowed = set(member_names)
        clean: list[str] = []
        for name in mentions or []:
            name = str(name or "")
            if name in allowed and name not in clean:
                clean.append(name)
        meta["mentions"] = clean
        fts_body = ""
    else:
        mentions = extract_mentions(body, member_names)
        meta["mentions"] = mentions
        fts_body = body
        meta.pop("e2e", None)
    meta_json = json.dumps(meta)

    def write(box, _m):
        box.conn.execute(
            "UPDATE messages SET body = ?, metadata_json = ?, edited_at = ? WHERE id = ?",
            (body, meta_json, now, message_id),
        )
        _fts_upsert(box, message_id, fts_body, _meta_name(meta))

    _fanout_members(members, write)
    updated = get_message(actor, message_id)
    return updated


def delete_message(*, actor: str, conversation_id: str, message_id: str) -> dict | None:
    current = get_message(actor, message_id)
    if current is None or current["conversation_id"] != conversation_id:
        return None
    if current["sender_username"] != actor:
        return None
    members = members_of(actor, conversation_id)
    meta = current.get("metadata") or {}
    owned = [
        a
        for a in attachment_entries(meta)
        if (a.get("owner_username") or actor) == actor
    ]

    def write(box, _m):
        box.conn.execute("DELETE FROM reactions WHERE message_id = ?", (message_id,))
        box.conn.execute(
            "DELETE FROM pins WHERE conversation_id = ? AND message_id = ?",
            (conversation_id, message_id),
        )
        box.conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        _fts_delete(box, message_id)

    _fanout_members(members, write)
    for att in owned:
        try:
            delete_upload_if_owned(
                owner_username=att.get("owner_username") or actor,
                owner_rel=att.get("owner_rel") or "",
                conversation_id=conversation_id,
            )
        except (OSError, ValueError):
            logger.debug("chat upload unlink failed", exc_info=True)
    return {"id": message_id, "conversation_id": conversation_id}


def mark_read(
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
    decrypted: bool = False,
) -> None:
    msg = get_message(actor, message_id)
    if msg is None:
        return
    now = _now_iso()
    with mailbox(actor) as box:
        cur = box.conn.execute(
            "SELECT last_read_id, last_read_at FROM read_state WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        same_message = False
        if cur:
            last_id, last_at = cur[0], cur[1]
            current = (last_at, last_id)
            incoming = (msg["created_at"], message_id)
            if incoming < current:
                return
            same_message = incoming == current
        if not same_message:
            box.conn.execute(
                """
                INSERT INTO read_state (conversation_id, last_read_id, last_read_at)
                VALUES (?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    last_read_id = excluded.last_read_id,
                    last_read_at = excluded.last_read_at
                """,
                (conversation_id, message_id, msg["created_at"]),
            )
        box.conn.commit()
    members = members_of(actor, conversation_id)

    def write(box, m):
        if m["username"] == actor:
            return
        box.conn.execute(
            """
            INSERT INTO receipts (
                conversation_id, username, last_read_id, last_read_at, decrypted
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id, username) DO UPDATE SET
                last_read_id = excluded.last_read_id,
                last_read_at = excluded.last_read_at,
                decrypted = CASE
                    WHEN receipts.last_read_id = excluded.last_read_id
                    THEN MAX(receipts.decrypted, excluded.decrypted)
                    ELSE excluded.decrypted
                END
            """,
            (conversation_id, actor, message_id, now, int(decrypted)),
        )

    _fanout_members(members, write)


def list_receipts(username: str, conversation_id: str) -> list[dict]:
    with mailbox(username) as box:
        rows = box.conn.execute(
            """
            SELECT username, last_read_id, last_read_at, decrypted FROM receipts
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchall()
        return [
            {
                "username": r[0],
                "last_read_id": r[1],
                "last_read_at": r[2],
                "decrypted": bool(r[3]),
            }
            for r in rows
        ]


def unread_total(username: str) -> int:
    with mailbox(username) as box:
        convs = box.conn.execute("SELECT id FROM conversations").fetchall()
        return sum(_unread_count(box.conn, r[0], username) for r in convs)


def unread_summary(central: sqlite3.Connection | None, username: str) -> dict:
    if central is not None:
        maybe_migrate_user(central, username)
    convos = list_conversations(central, username) if central is not None else None
    if convos is None:
        total = unread_total(username)
        return {"total": total, "conversations": []}
    items = [{"id": c["id"], "count": c["unread"]} for c in convos if c["unread"]]
    return {"total": sum(c["unread"] for c in convos), "conversations": items}


def toggle_reaction(*, actor: str, conversation_id: str, message_id: str, emoji: str) -> list[dict]:
    emoji = (emoji or "").strip()[:16]
    if not emoji:
        raise ValueError("emoji required")
    current = get_message(actor, message_id)
    if current is None or current["conversation_id"] != conversation_id:
        raise ValueError("Message not found")
    members = members_of(actor, conversation_id)
    existing = next((r for r in current["reactions"] if r["username"] == actor), None)

    def write(box, _m):
        if existing and existing["emoji"] == emoji:
            box.conn.execute(
                "DELETE FROM reactions WHERE message_id = ? AND username = ?",
                (message_id, actor),
            )
        else:
            box.conn.execute(
                """
                INSERT INTO reactions (message_id, username, emoji)
                VALUES (?, ?, ?)
                ON CONFLICT(message_id, username) DO UPDATE SET emoji = excluded.emoji
                """,
                (message_id, actor, emoji),
            )

    _fanout_members(members, write)
    updated = get_message(actor, message_id)
    return updated["reactions"] if updated else []


def list_pins(username: str, conversation_id: str) -> list[dict]:
    with mailbox(username) as box:
        rows = box.conn.execute(
            """
            SELECT p.message_id, p.pinned_by, p.pinned_at, m.body, m.msg_type, m.metadata_json, m.sender_username
            FROM pins p
            LEFT JOIN messages m ON m.id = p.message_id
            WHERE p.conversation_id = ?
            ORDER BY p.pinned_at ASC
            """,
            (conversation_id,),
        ).fetchall()
        out = []
        for r in rows:
            meta = _loads(r[5])
            if is_e2e_meta(meta):
                preview = ENCRYPTED_PREVIEW
            else:
                preview = plain_preview(r[3] or "") if r[4] == "text" else (meta.get("original_name") or "File")
            out.append(
                {
                    "message_id": r[0],
                    "pinned_by": r[1],
                    "pinned_at": r[2],
                    "preview": preview,
                    "sender_username": r[6],
                }
            )
        return out


def pin_message(*, actor: str, conversation_id: str, message_id: str) -> list[dict]:
    current = get_message(actor, message_id)
    if current is None or current["conversation_id"] != conversation_id:
        raise ValueError("Message not found")
    pins = list_pins(actor, conversation_id)
    if any(p["message_id"] == message_id for p in pins):
        return pins
    if len(pins) >= MAX_PINS:
        raise ValueError(f"At most {MAX_PINS} pinned messages")
    members = members_of(actor, conversation_id)
    now = _now_iso()

    def write(box, _m):
        box.conn.execute(
            """
            INSERT OR IGNORE INTO pins (conversation_id, message_id, pinned_by, pinned_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, message_id, actor, now),
        )

    _fanout_members(members, write)
    return list_pins(actor, conversation_id)


def unpin_message(*, actor: str, conversation_id: str, message_id: str) -> list[dict]:
    members = members_of(actor, conversation_id)

    def write(box, _m):
        box.conn.execute(
            "DELETE FROM pins WHERE conversation_id = ? AND message_id = ?",
            (conversation_id, message_id),
        )

    _fanout_members(members, write)
    return list_pins(actor, conversation_id)


def search_messages(username: str, query: str, *, limit: int = 40) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(50, int(limit)))
    with mailbox(username) as box:
        rows = []
        if box.fts:
            try:
                rows = box.conn.execute(
                    """
                    SELECT m.id, m.conversation_id, m.sender_username, m.sender_id, m.msg_type, m.body,
                           m.metadata_json, m.reply_to_id, m.edited_at, m.created_at
                    FROM messages_fts f
                    JOIN messages m ON m.id = f.message_id
                    WHERE messages_fts MATCH ?
                    ORDER BY m.created_at DESC
                    LIMIT ?
                    """,
                    (q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            like = f"%{q}%"
            rows = box.conn.execute(
                """
                SELECT id, conversation_id, sender_username, sender_id, msg_type, body,
                       metadata_json, reply_to_id, edited_at, created_at
                FROM messages
                WHERE body LIKE ? OR metadata_json LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (like, like, limit),
            ).fetchall()
        results = []
        for r in rows:
            msg = _row_to_message(r)
            if msg.get("e2e"):
                continue
            msg["snippet"] = plain_preview(msg["body"] or _meta_name(msg["metadata"]), 160)
            results.append(msg)
        return results


def list_shared_with_me(central: sqlite3.Connection, username: str) -> list[dict]:
    maybe_migrate_user(central, username)
    with mailbox(username) as box:
        rows = box.conn.execute(
            """
            SELECT id, conversation_id, sender_username, metadata_json, created_at
            FROM messages
            WHERE msg_type IN ('file', 'gif') AND sender_username != ?
            ORDER BY created_at DESC
            """,
            (username,),
        ).fetchall()
        out = []
        for r in rows:
            meta = _loads(r[3])
            if meta.get("hidden"):
                continue
            entries = attachment_entries(meta)
            if not entries:
                continue
            for i, att in enumerate(entries):
                share_url = att.get("share_url") or (meta.get("share_url") if i == 0 else None)
                file_url = share_url or f"/api/chat/conversations/{r[1]}/messages/{r[0]}/file"
                if not share_url and i:
                    file_url = f"{file_url}?i={i}"
                out.append(
                    {
                        "id": r[0],
                        "conversation_id": r[1],
                        "sender_username": r[2],
                        "original_name": att.get("original_name") or "file",
                        "relative_path": att.get("owner_rel"),
                        "size_bytes": att.get("size_bytes") or 0,
                        "created_at": r[4],
                        "file_url": file_url,
                        "share_url": share_url,
                        "share_id": att.get("share_id") or (meta.get("share_id") if i == 0 else None),
                        "media_kind": att.get("media_kind"),
                        "saved_rel": att.get("saved_rel"),
                        "index": i,
                    }
                )
        return out


def list_shared_with_me_grouped(central: sqlite3.Connection, username: str) -> list[dict]:
    items = list_shared_with_me(central, username)
    groups: dict[str, dict] = {}
    for item in items:
        sender = item["sender_username"]
        if sender not in groups:
            groups[sender] = {
                "sender_username": sender,
                "folder_path": "",
                "files": [],
            }
        groups[sender]["files"].append(item)
    result = list(groups.values())
    result.sort(key=lambda g: g["files"][0]["created_at"] if g["files"] else "", reverse=True)
    return result


def patch_attachment_share(
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
    index: int,
    share_id: str,
    share_url: str,
) -> dict | None:
    """Persist a static-share link onto an attachment in every member mailbox."""
    current = get_message(actor, message_id)
    if current is None or current["conversation_id"] != conversation_id:
        return None
    meta = dict(current.get("metadata") or {})
    atts = [dict(a) for a in attachment_entries(meta)]
    if index < 0 or index >= len(atts):
        return None
    atts[index]["share_id"] = share_id
    atts[index]["share_url"] = share_url
    meta["attachments"] = atts
    if index == 0:
        meta["share_id"] = share_id
        meta["share_url"] = share_url
    meta_json = json.dumps(meta)
    members = members_of(actor, conversation_id)

    def write(box, _m):
        box.conn.execute(
            "UPDATE messages SET metadata_json = ? WHERE id = ?",
            (meta_json, message_id),
        )

    _fanout_members(members, write)
    return get_message(actor, message_id)


def hide_shared_item(username: str, message_id: str) -> dict | None:
    """Recipient dismisses a link from Shared with me (does not delete the author's file)."""
    msg = get_message(username, message_id)
    if msg is None:
        return None
    meta = dict(msg.get("metadata") or {})
    meta["hidden"] = True
    with mailbox(username) as box:
        box.conn.execute(
            "UPDATE messages SET metadata_json = ? WHERE id = ?",
            (json.dumps(meta), message_id),
        )
        box.conn.commit()
    return msg


def set_saved_rel(username: str, message_id: str, saved_rel: str, index: int = 0) -> None:
    msg = get_message(username, message_id)
    if msg is None:
        return
    meta = dict(msg.get("metadata") or {})
    atts = attachment_entries(meta)
    if 0 <= index < len(atts):
        atts[index] = dict(atts[index])
        atts[index]["saved_rel"] = saved_rel
        meta["attachments"] = atts
    if index == 0:
        meta["saved_rel"] = saved_rel
    with mailbox(username) as box:
        box.conn.execute(
            "UPDATE messages SET metadata_json = ? WHERE id = ?",
            (json.dumps(meta), message_id),
        )
        box.conn.commit()


def other_member_id(username: str, conversation_id: str, user_id: int) -> int | None:
    conv = get_conversation(username, conversation_id)
    if not conv:
        return None
    for m in conv["members"]:
        if m["user_id"] != user_id:
            return m["user_id"]
    return None


def member_user_ids(username: str, conversation_id: str) -> list[int]:
    return [m["user_id"] for m in members_of(username, conversation_id)]


def get_e2e_wraps(username: str, conversation_id: str) -> dict[str, dict]:
    with mailbox(username) as box:
        try:
            rows = box.conn.execute(
                "SELECT username, wrap_json FROM e2e_wraps WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        out: dict[str, dict] = {}
        for name, raw in rows:
            try:
                out[str(name)] = parse_wrap(json.loads(raw) if isinstance(raw, str) else raw)
            except (ValueError, json.JSONDecodeError, TypeError):
                continue
        return out


def put_e2e_wraps(*, actor: str, conversation_id: str, wraps: dict) -> dict[str, dict]:
    conv = get_conversation(actor, conversation_id)
    if conv is None:
        raise ValueError("Conversation not found")
    members = conv["members"]
    allowed = {m["username"] for m in members}
    if not isinstance(wraps, dict) or not wraps:
        raise ValueError("wraps required")
    if len(wraps) > len(allowed) + 2:
        raise ValueError("Too many wraps")
    clean: dict[str, dict] = {}
    for name, blob in wraps.items():
        name = str(name or "")
        if name not in allowed:
            continue
        clean[name] = parse_wrap(blob)
    if not clean:
        raise ValueError("No valid wraps")

    def write(box, _m):
        for name, wrap in clean.items():
            box.conn.execute(
                """
                INSERT INTO e2e_wraps (conversation_id, username, wrap_json)
                VALUES (?, ?, ?)
                ON CONFLICT(conversation_id, username) DO UPDATE SET wrap_json = excluded.wrap_json
                """,
                (conversation_id, name, json.dumps(wrap, separators=(",", ":"))),
            )

    _fanout_members(members, write)
    return get_e2e_wraps(actor, conversation_id)



def peer_user_ids_for_presence(username: str) -> list[int]:
    """All other user ids that share a conversation with this user."""
    ids: set[int] = set()
    with mailbox(username) as box:
        rows = box.conn.execute(
            "SELECT user_id FROM members WHERE username != ?",
            (username,),
        ).fetchall()
        for (uid,) in rows:
            ids.add(int(uid))
    return list(ids)


# Compatibility aliases used by older call sites / tests
def find_dm_conversation_ids(conn: sqlite3.Connection, user_a_id: int, user_b_id: int) -> str | None:
    a = _username_for_id(conn, user_a_id)
    b = _username_for_id(conn, user_b_id)
    if not a or not b:
        return None
    return find_dm_conversation(a, b)
