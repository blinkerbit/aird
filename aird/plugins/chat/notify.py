"""Push chat notifications after messages are persisted."""

from __future__ import annotations

import logging

from aird.plugins.chat import db as chat_db
from aird.plugins.chat.service import get_chat_hub, message_preview

logger = logging.getLogger(__name__)


def _send_web_push(username: str, payload: dict) -> None:
    """Best-effort Web Push for installed PWAs / background browsers."""
    try:
        import aird.constants as constants_module
        from aird.pwa.push import send_web_push

        conn = getattr(constants_module, "DB_CONN", None)
        send_web_push(conn, username, payload)
    except Exception:
        logger.debug("web push skipped for %s", username, exc_info=True)


def dispatch_message(msg: dict, *, sender_id: int, sender_username: str) -> None:
    conv_id = msg["conversation_id"]
    members = chat_db.members_of(sender_username, conv_id)
    user_ids = [m["user_id"] for m in members]
    get_chat_hub().broadcast_user_ids(
        user_ids,
        {"type": "chat_message", "message": msg},
    )
    mentions = set(msg.get("mentions") or [])
    for m in members:
        if m["user_id"] == sender_id:
            continue
        if chat_db.is_muted(m["username"], conv_id) and m["username"] not in mentions:
            continue
        try:
            total = chat_db.unread_total(m["username"])
        except Exception:
            total = 0
        preview = message_preview(msg)
        notify = {
            "type": "chat_notify",
            "conversation_id": conv_id,
            "message_id": msg["id"],
            "sender": msg.get("sender_username"),
            "msg_type": msg.get("msg_type"),
            "preview": preview,
            "unread_total": total,
            "mention": m["username"] in mentions,
        }
        get_chat_hub().send_to_user(m["user_id"], notify)
        _send_web_push(
            m["username"],
            {
                "title": msg.get("sender_username") or "Aird",
                "body": preview,
                "sender": msg.get("sender_username"),
                "preview": preview,
                "conversation_id": conv_id,
                "url": f"/chat?c={conv_id}",
                "tag": f"aird-chat-{conv_id}",
            },
        )


def dispatch_message_deleted(username: str, conversation_id: str, message_id: str) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {
            "type": "chat_message_deleted",
            "conversation_id": conversation_id,
            "message_id": message_id,
        },
    )


def dispatch_message_edited(username: str, msg: dict) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, msg["conversation_id"]),
        {"type": "chat_message_edited", "message": msg},
    )


def dispatch_reaction(username: str, conversation_id: str, message_id: str, reactions: list) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {
            "type": "chat_reaction",
            "conversation_id": conversation_id,
            "message_id": message_id,
            "reactions": reactions,
        },
    )


def dispatch_receipt(username: str, conversation_id: str, message_id: str, actor_id: int) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {
            "type": "chat_receipt",
            "conversation_id": conversation_id,
            "username": username,
            "last_read_id": message_id,
        },
        exclude_user_id=actor_id,
    )


def dispatch_pin(username: str, conversation_id: str, pins: list, *, pinned: bool) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {
            "type": "chat_pin" if pinned else "chat_unpin",
            "conversation_id": conversation_id,
            "pins": pins,
        },
    )


def dispatch_presence(username: str, user_id: int, online: bool) -> None:
    peers = chat_db.peer_user_ids_for_presence(username)
    get_chat_hub().broadcast_user_ids(
        peers,
        {"type": "chat_presence", "user_id": user_id, "username": username, "online": online},
    )


def dispatch_typing(username: str, conversation_id: str, actor_id: int) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {"type": "chat_typing", "conversation_id": conversation_id, "username": username},
        exclude_user_id=actor_id,
    )


def dispatch_e2e_need_key(username: str, conversation_id: str, actor_id: int) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {
            "type": "chat_e2e_need_key",
            "conversation_id": conversation_id,
            "username": username,
        },
        exclude_user_id=actor_id,
    )


def dispatch_e2e_key_ready(username: str, conversation_id: str) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {"type": "chat_e2e_key_ready", "conversation_id": conversation_id},
    )
