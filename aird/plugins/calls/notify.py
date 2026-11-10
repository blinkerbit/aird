"""Call signaling over the chat hub (no media)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time

from aird.notifications.engine import deliver, drop_pending_calls, push_now
from aird.plugins.chat import db as chat_db
from aird.plugins.chat.service import get_chat_hub

logger = logging.getLogger(__name__)
_RING_TTL = 45
_MISSED_GRACE = 2
_STATE_KEEP_S = 3600
_MISSED_KEEP_S = 900


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
    conn.execute(
        """CREATE TABLE IF NOT EXISTS call_state (
            call_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            caller TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(call_state)").fetchall()}
    if "mode" not in cols:
        conn.execute("ALTER TABLE call_state ADD COLUMN mode TEXT NOT NULL DEFAULT 'audio'")
    if "targets" not in cols:
        conn.execute("ALTER TABLE call_state ADD COLUMN targets TEXT NOT NULL DEFAULT ''")
    return conn


def _record_call(
    call_id: str,
    conversation_id: str,
    caller: str,
    mode: str = "audio",
    targets: list[str] | None = None,
) -> None:
    """Remember a ringing call so any worker can tell if it was answered."""
    if not call_id:
        return
    conn = _ring_conn()
    if conn is None:
        return
    try:
        now = time.time()
        conn.execute("DELETE FROM call_state WHERE created_at < ?", (now - _STATE_KEEP_S,))
        conn.execute(
            "INSERT OR REPLACE INTO call_state"
            "(call_id, conversation_id, caller, state, created_at, mode, targets) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                call_id,
                conversation_id,
                caller,
                "ringing",
                now,
                "video" if mode == "video" else "audio",
                json.dumps([str(name) for name in (targets or []) if str(name).strip()]),
            ),
        )
        conn.commit()
    except Exception:
        logger.debug("call state save failed", exc_info=True)
    finally:
        conn.close()


def _settle_call(conversation_id: str, call_id: str, state: str) -> None:
    """Move a ringing call to answered, ended or missed. Other states stay put."""
    conn = _ring_conn()
    if conn is None:
        return
    try:
        if call_id:
            conn.execute(
                "UPDATE call_state SET state=? WHERE call_id=? AND state='ringing'",
                (state, call_id),
            )
        else:
            conn.execute(
                "UPDATE call_state SET state=? WHERE conversation_id=? AND state='ringing'",
                (state, conversation_id),
            )
        conn.commit()
    except Exception:
        logger.debug("call state update failed", exc_info=True)
    finally:
        conn.close()


def _ringing_row(conn: sqlite3.Connection, call_id: str) -> dict | None:
    row = conn.execute(
        "SELECT conversation_id, caller, mode, targets FROM call_state "
        "WHERE call_id=? AND state='ringing'",
        (call_id,),
    ).fetchone()
    if not row:
        return None
    try:
        targets = json.loads(row[3] or "[]")
    except json.JSONDecodeError:
        targets = []
    if not isinstance(targets, list):
        targets = []
    return {
        "conversation_id": str(row[0]),
        "caller": str(row[1]),
        "mode": "video" if row[2] == "video" else "audio",
        "targets": [str(name) for name in targets if str(name).strip()],
        "call_id": call_id,
    }


def _peek_ringing(call_id: str) -> dict | None:
    if not call_id:
        return None
    conn = _ring_conn()
    if conn is None:
        return None
    try:
        return _ringing_row(conn, call_id)
    except Exception:
        return None
    finally:
        conn.close()


def _claim_missed(call_id: str) -> dict | None:
    """Atomically flip a still-ringing call to missed. Returns the call record."""
    conn = _ring_conn()
    if conn is None:
        return None
    try:
        conn.execute("BEGIN IMMEDIATE")
        claimed = _ringing_row(conn, call_id)
        if not claimed:
            conn.rollback()
            return None
        conn.execute("UPDATE call_state SET state='missed' WHERE call_id=?", (call_id,))
        conn.commit()
        return claimed
    except Exception:
        logger.debug("call missed claim failed", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()


def _expire_ring(caller: str, conversation_id: str, call_id: str) -> None:
    """Nobody answered in time. Tell everyone the call is missed."""
    claimed = _claim_missed(call_id)
    if not claimed or claimed["conversation_id"] != conversation_id:
        return
    try:
        ids = _member_ids(caller, conversation_id)
        get_chat_hub().broadcast_user_ids(
            ids,
            {
                "type": "call_missed",
                "conversation_id": conversation_id,
                "from": caller,
                "call_id": call_id,
                "mode": claimed["mode"],
            },
        )
        clear_call_rings(caller, conversation_id, call_id)
        _stop_remote_rings(caller, conversation_id, call_id)
        _leave_missed_notes(claimed)
    except Exception:
        logger.warning("call missed dispatch failed", exc_info=True)


def _leave_missed_notes(call: dict) -> None:
    """Put a small deletable missed-call banner in each person's chat."""
    caller = str(call.get("caller") or "")
    conversation_id = str(call.get("conversation_id") or "")
    call_id = str(call.get("call_id") or "")
    if not caller or not conversation_id or not call_id:
        return
    try:
        members = chat_db.members_of(caller, conversation_id)
    except Exception:
        logger.debug("missed call members failed", exc_info=True)
        return
    mode = "video" if call.get("mode") == "video" else "audio"
    label = "Missed video call" if mode == "video" else "Missed voice call"
    if len(members) > 2:
        label = f"{label} · {caller}"
    wanted = {str(name) for name in (call.get("targets") or []) if str(name).strip()}
    caller_id = 0
    for member in members:
        if str(member.get("username") or "") == caller:
            caller_id = int(member.get("user_id") or 0)
            break
    for member in members:
        name = str(member.get("username") or "")
        if not name or name == caller:
            continue
        if wanted and name not in wanted:
            continue
        try:
            msg = chat_db.insert_private_message(
                owner=name,
                sender_username=caller,
                sender_id=caller_id,
                conversation_id=conversation_id,
                body=label,
                call_id=call_id,
                mode=mode,
            )
        except Exception:
            logger.debug("missed call note failed", exc_info=True)
            continue
        if not msg:
            continue
        try:
            get_chat_hub().broadcast_user_ids(
                [int(member.get("user_id") or 0)],
                {"type": "chat_message", "message": msg},
            )
        except Exception:
            logger.debug("missed call note fanout failed", exc_info=True)


def _arm_ring_timeout(caller: str, conversation_id: str, call_id: str) -> None:
    if not call_id:
        return
    timer = threading.Timer(
        _RING_TTL + _MISSED_GRACE,
        _expire_ring,
        args=(caller, conversation_id, call_id),
    )
    timer.daemon = True
    timer.start()


def clear_user_ring(username: str, conversation_id: str, call_id: str = "") -> None:
    """Stop replaying a ring to one person, for example after they answered."""
    conn = _ring_conn()
    if conn is None:
        return
    try:
        if call_id:
            row = conn.execute(
                "SELECT payload FROM call_rings WHERE username=?", (username,)
            ).fetchone()
            if not row:
                return
            try:
                stored = json.loads(row[0])
            except json.JSONDecodeError:
                stored = {}
            stored_id = str(stored.get("call_id") or "")
            if stored_id and stored_id != call_id:
                return
        conn.execute("DELETE FROM call_rings WHERE username=?", (username,))
        conn.commit()
    except Exception:
        logger.debug("user ring clear failed", exc_info=True)
    finally:
        conn.close()


def save_call_rings(
    caller: str,
    conversation_id: str,
    mode: str,
    call_id: str,
    *,
    targets: list[str] | None = None,
) -> None:
    try:
        members = chat_db.members_of(caller, conversation_id)
        target_set = {str(t).strip() for t in (targets or []) if str(t).strip()}
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
                if target_set and name not in target_set:
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
    targets: list[str] | None = None,
) -> None:
    mode_name = "video" if mode == "video" else "audio"
    label = "Video call" if mode_name == "video" else "Voice call"
    # Store the ring first. A device that gets the invite can then ask
    # /api/chat/call-ring whether the call is still live.
    save_call_rings(caller_username, conversation_id, mode, call_id, targets=targets)
    _record_call(call_id, conversation_id, caller_username, mode_name, targets)
    _arm_ring_timeout(caller_username, conversation_id, call_id)
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
    target_set = {str(t).strip() for t in (targets or []) if str(t).strip()}
    for member in chat_db.members_of(caller_username, conversation_id):
        if member["user_id"] == caller_id or member["username"] == caller_username:
            continue
        if target_set and member["username"] not in target_set:
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


def dispatch_call_invite_more(
    *,
    conversation_id: str,
    caller_username: str,
    caller_id: int,
    mode: str,
    room: str,
    call_id: str,
    targets: list[str] | None = None,
) -> None:
    """Ring additional participants into an existing call."""
    if not targets:
        return
    mode_name = "video" if mode == "video" else "audio"
    label = "Group video call" if mode_name == "video" else "Group voice call"
    save_call_rings(caller_username, conversation_id, mode, call_id, targets=targets)
    socket_payload = {
        "type": "call_invite",
        "conversation_id": conversation_id,
        "room": room,
        "mode": mode_name,
        "from": caller_username,
        "call_id": call_id,
        "ttl": _RING_TTL,
        "group": True,
    }
    push_payload = {
        "title": label,
        "body": f"{caller_username} invited you to a group call.",
        "sender": caller_username,
        "preview": label,
        "mode": mode_name,
        "kind": "call",
        "call_id": call_id,
        "conversation_id": conversation_id,
        "url": f"/chat?c={conversation_id}",
        "tag": f"aird-call.{conversation_id}.{call_id}.{mode_name}",
    }
    target_set = {str(t).strip() for t in targets if str(t).strip()}
    for member in chat_db.members_of(caller_username, conversation_id):
        if member["username"] not in target_set:
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


def call_status(call_id: str) -> str:
    """ringing / answered / ended / missed, or empty if unknown."""
    if not call_id:
        return ""
    conn = _ring_conn()
    if conn is None:
        return ""
    try:
        row = conn.execute(
            "SELECT state FROM call_state WHERE call_id=?", (call_id,)
        ).fetchone()
        return str(row[0]) if row else ""
    except Exception:
        return ""
    finally:
        conn.close()


def peek_missed(username: str) -> dict | None:
    """A recent missed call for this user, for devices that were offline when it rang out."""
    if not username:
        return None
    conn = _ring_conn()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT call_id, conversation_id, caller FROM call_state "
            "WHERE state='missed' AND caller != ? AND created_at > ? "
            "ORDER BY created_at DESC LIMIT 8",
            (username, time.time() - _MISSED_KEEP_S),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    for call_id, conversation_id, caller in rows:
        try:
            members = chat_db.members_of(str(caller), str(conversation_id))
        except Exception:
            continue
        if any(str(member.get("username") or "") == username for member in members):
            return {
                "type": "call_missed",
                "conversation_id": str(conversation_id),
                "from": str(caller),
                "call_id": str(call_id),
            }
    return None


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
        _settle_call(conversation_id, ended_id, "ended")
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
    if signal_type == "call_invite_more":
        targets = (extra or {}).get("targets")
        if isinstance(targets, list) and targets:
            mode = str((extra or {}).get("mode") or "audio").lower()
            room = str((extra or {}).get("room") or "")
            cid = str((extra or {}).get("call_id") or "")
            dispatch_call_invite_more(
                conversation_id=conversation_id,
                caller_username=username,
                caller_id=actor_id,
                mode=mode,
                room=room or f"aird-call-{cid}",
                call_id=cid,
                targets=[str(t) for t in targets],
            )
        return
    if signal_type == "call_accept":
        accepted_id = str((extra or {}).get("call_id") or "")
        _settle_call(conversation_id, accepted_id, "answered")
        # The answering device is already in the call. Tell the caller.
        get_chat_hub().broadcast_user_ids(ids, payload, exclude_user_id=actor_id)
        # Every other device of the person who answered stops ringing.
        answered = {
            "type": "call_answered",
            "conversation_id": conversation_id,
            "from": username,
        }
        if accepted_id:
            answered["call_id"] = accepted_id
        get_chat_hub().broadcast_user_ids([actor_id], answered)
        clear_user_ring(username, conversation_id, accepted_id)
        try:
            drop_pending_calls(conversation_id, accepted_id, username)
        except Exception:
            logger.warning("answered call push cancel failed", exc_info=True)
        return
    get_chat_hub().broadcast_user_ids(ids, payload)
    ended_id = str((extra or {}).get("call_id") or "")
    ringing = _peek_ringing(ended_id)
    _settle_call(conversation_id, ended_id, "ended")
    clear_call_rings(username, conversation_id, ended_id)
    _stop_remote_rings(username, conversation_id, ended_id)
    if ringing and ringing.get("caller") == username:
        _leave_missed_notes(ringing)


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


