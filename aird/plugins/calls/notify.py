"""Call signaling over the chat hub (no media)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from aird.notifications.engine import deliver, drop_pending_calls, push_now
from aird.plugins.chat import db as chat_db
from aird.plugins.chat.service import get_chat_hub

logger = logging.getLogger(__name__)
_RING_TTL = 45


def _ring_conn():
    import aird.constants as constants_module

    path = getattr(constants_module, "DB_PATH", None)
    if not path:
        return None
    conn = sqlite3.connect(path, timeout=5)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS call_rings (
            username TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    return conn


def save_call_rings(caller: str, conversation_id: str, mode: str, call_id: str) -> None:
    try:
        members = chat_db.members_of(caller, conversation_id)
        payload = json.dumps({
            "type": "call_invite",
            "conversation_id": conversation_id,
            "from": caller,
            "mode": "video" if mode == "video" else "audio",
            "call_id": call_id,
        })
        now = time.time()
        conn = _ring_conn()
        if conn is None:
            return
        try:
            for member in members:
                name = member["username"]
                if name == caller:
                    continue
                conn.execute(
                    """INSERT INTO call_rings(username, payload, created_at) VALUES(?,?,?)
                       ON CONFLICT(username) DO UPDATE SET
                         payload=excluded.payload,
                         created_at=excluded.created_at""",
                    (name, payload, now),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("call ring save failed", exc_info=True)


def clear_call_rings(username: str, conversation_id: str, call_id: str = "") -> None:
    try:
        members = chat_db.members_of(username, conversation_id)
        conn = _ring_conn()
        if conn is None:
            return
        try:
            for member in members:
                if call_id:
                    row = conn.execute(
                        "SELECT payload FROM call_rings WHERE username=?",
                        (member["username"],),
                    ).fetchone()
                    if not row:
                        continue
                    try:
                        stored = json.loads(row[0])
                    except json.JSONDecodeError:
                        stored = {}
                    stored_id = str(stored.get("call_id") or "")
                    if stored_id and stored_id != call_id:
                        continue
                conn.execute("DELETE FROM call_rings WHERE username=?", (member["username"],))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("call ring clear failed", exc_info=True)


def read_call_ring(conn: sqlite3.Connection, username: str) -> dict | None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS call_rings (
            username TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    row = conn.execute(
        "SELECT payload, created_at FROM call_rings WHERE username=?",
        (username,),
    ).fetchone()
    if not row:
        return None
    if time.time() - float(row[1]) > _RING_TTL:
        return None
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("conversation_id"):
        return None
    data["type"] = "call_invite"
    return data


def _member_ids(username: str, conversation_id: str) -> list[int]:
    return chat_db.member_user_ids(username, conversation_id)


def dispatch_call_invite(
    *,
    conversation_id: str,
    caller_username: str,
    caller_id: int,
    mode: str,
    room: str,
    call_id: str,
) -> None:
    mode_name = "video" if mode == "video" else "audio"
    label = "Video call" if mode_name == "video" else "Voice call"
    # Store the ring first. A device that gets the invite can then ask
    # /api/chat/call-ring whether the call is still live.
    save_call_rings(caller_username, conversation_id, mode, call_id)
    socket_payload = {
        "type": "call_invite",
        "conversation_id": conversation_id,
        "room": room,
        "mode": mode_name,
        "from": caller_username,
        "call_id": call_id,
        "ttl": _RING_TTL,
    }
    push_payload = {
        "title": label,
        "body": f"{caller_username} is calling. Accept or decline.",
        "sender": caller_username,
        "preview": label,
        "mode": mode_name,
        "kind": "call",
        "call_id": call_id,
        "conversation_id": conversation_id,
        "url": f"/chat?c={conversation_id}",
        "tag": f"aird-call.{conversation_id}.{call_id}.{mode_name}",
    }
    for member in chat_db.members_of(caller_username, conversation_id):
        if member["user_id"] == caller_id or member["username"] == caller_username:
            continue
        try:
            muted = chat_db.is_muted(member["username"], conversation_id)
        except Exception:
            muted = False
        deliver(
            username=member["username"],
            user_id=member["user_id"],
            kind="call",
            socket_payload=socket_payload,
            push_payload=push_payload,
            push=not muted,
            expire_s=45,
            push_ttl=30,
            immediate=True,
        )


def peek_call_ring(username: str) -> dict | None:
    conn = _ring_conn()
    if conn is None:
        return None
    try:
        return read_call_ring(conn, username)
    finally:
        conn.close()


def dispatch_call_signal(
    *,
    username: str,
    conversation_id: str,
    actor_id: int,
    signal_type: str,
    extra: dict | None = None,
) -> None:
    if signal_type == "call_decline":
        ids = _member_ids(username, conversation_id)
        payload = {
            "type": "call_decline",
            "conversation_id": conversation_id,
            "from": username,
        }
        if extra:
            payload.update(extra)
        get_chat_hub().broadcast_user_ids(ids, payload)
        ended_id = str((extra or {}).get("call_id") or "")
        clear_call_rings(username, conversation_id, ended_id)
        _stop_remote_rings(username, conversation_id, ended_id)
        return
    ids = _member_ids(username, conversation_id)
    payload = {
        "type": signal_type,
        "conversation_id": conversation_id,
        "from": username,
    }
    if extra:
        payload.update(extra)
    if signal_type == "call_mode":
        mode = str((extra or {}).get("mode") or "audio").lower()
        if mode not in ("audio", "video"):
            mode = "audio"
        payload["mode"] = mode
        get_chat_hub().broadcast_user_ids(ids, payload)
        return
    if signal_type == "call_accept":
        # The answering device is already in the call. Tell the caller.
        # Leave the ring in place so every other signed-in device keeps ringing.
        get_chat_hub().broadcast_user_ids(ids, payload, exclude_user_id=actor_id)
        return
    get_chat_hub().broadcast_user_ids(ids, payload)
    ended_id = str((extra or {}).get("call_id") or "")
    clear_call_rings(username, conversation_id, ended_id)
    _stop_remote_rings(username, conversation_id, ended_id)


def _stop_remote_rings(username: str, conversation_id: str, call_id: str) -> None:
    """Stop every other device now. The socket message already went out."""
    try:
        drop_pending_calls(conversation_id, call_id)
    except Exception:
        logger.warning("pending call push cancel failed", exc_info=True)
    try:
        members = chat_db.members_of(username, conversation_id)
    except Exception:
        logger.warning("call end members failed", exc_info=True)
        return
    push_payload = {
        "title": "Call ended",
        "body": "Call ended",
        "kind": "call_end",
        "type": "call_end",
        "conversation_id": conversation_id,
        "call_id": call_id,
        "tag": f"aird-call-end-{call_id or conversation_id}",
    }
    for member in members:
        if member["username"] == username:
            continue
        push_now(member["username"], push_payload, ttl=30)


