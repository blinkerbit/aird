"""Call signaling over the chat hub (no media)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

from aird.plugins.chat import db as chat_db
from aird.plugins.chat.service import get_chat_hub

logger = logging.getLogger(__name__)
_push_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="call-push")
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


def clear_call_rings(username: str, conversation_id: str) -> None:
    try:
        members = chat_db.members_of(username, conversation_id)
        conn = _ring_conn()
        if conn is None:
            return
        try:
            for member in members:
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
    ids = _member_ids(caller_username, conversation_id)
    payload = {
        "type": "call_invite",
        "conversation_id": conversation_id,
        "room": room,
        "mode": mode,
        "from": caller_username,
        "call_id": call_id,
    }
    get_chat_hub().broadcast_user_ids(ids, payload, exclude_user_id=caller_id)
    save_call_rings(caller_username, conversation_id, mode, call_id)
    _push_pool.submit(_maybe_push_ring, caller_username, conversation_id, mode, call_id)


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
        clear_call_rings(username, conversation_id)
        return
    ids = _member_ids(username, conversation_id)
    payload = {
        "type": signal_type,
        "conversation_id": conversation_id,
        "from": username,
    }
    if extra:
        payload.update(extra)
    if signal_type == "call_accept":
        # The answering device is already in the call. Tell the caller.
        # Leave the ring in place so every other signed-in device keeps ringing.
        get_chat_hub().broadcast_user_ids(ids, payload, exclude_user_id=actor_id)
        return
    get_chat_hub().broadcast_user_ids(ids, payload)
    clear_call_rings(username, conversation_id)


def _maybe_push_ring(caller: str, conversation_id: str, mode: str, call_id: str = "") -> None:
    try:
        import aird.constants as constants_module
        from aird.pwa.push import send_web_push

        path = getattr(constants_module, "DB_PATH", None)
        if not path:
            return
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            members = chat_db.members_of(caller, conversation_id)
            label = "Video call" if mode == "video" else "Voice call"
            for m in members:
                if m["username"] == caller:
                    continue
                if chat_db.is_muted(m["username"], conversation_id):
                    continue
                send_web_push(
                    conn,
                    m["username"],
                    {
                        "title": label,
                        "body": f"{caller} is calling. Accept or decline.",
                        "sender": caller,
                        "preview": label,
                        "mode": "video" if mode == "video" else "audio",
                        "conversation_id": conversation_id,
                        "url": f"/chat?c={conversation_id}",
                    "tag": f"aird-call-{conversation_id}",
                    "kind": "call",
                    "call_id": call_id,
                    },
                )
        finally:
            conn.close()
    except Exception:
        logger.debug("call ring push skipped", exc_info=True)
