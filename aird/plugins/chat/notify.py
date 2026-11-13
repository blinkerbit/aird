"""Push chat notifications after messages are persisted."""

from __future__ import annotations

import logging

from aird.notifications.engine import deliver
from aird.plugins.chat import db as chat_db
from aird.plugins.chat.e2e import is_e2e_meta
from aird.plugins.chat.files import attachment_entries
from aird.plugins.chat.service import get_chat_hub, message_preview

logger = logging.getLogger(__name__)


def dispatch_message(msg: dict, *, sender_id: int, sender_username: str) -> None:
    conv_id = msg["conversation_id"]
    members = chat_db.members_of(sender_username, conv_id)
    user_ids = [m["user_id"] for m in members]
    get_chat_hub().broadcast_user_ids(
        user_ids,
        {"type": "chat_message", "message": msg},
    )
    mentions = set(msg.get("mentions") or [])
    meta = msg.get("metadata") or {}
    sealed = meta.get("e2e") if is_e2e_meta(meta) else None
    attachment_count = len(attachment_entries(meta))
    for m in members:
        if sender_id is not None and m["user_id"] == sender_id:
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
        # Socket only. Lets a client that holds the chat key show real text.
        if sealed:
            notify["e2e"] = sealed
        if attachment_count:
            notify["attachments"] = attachment_count
        deliver(
            username=m["username"],
            user_id=m["user_id"],
            kind="message",
            socket_payload=notify,
            push_payload={
                "title": msg.get("sender_username") or "Aird",
                "body": preview,
                "sender": msg.get("sender_username"),
                "preview": preview,
                "conversation_id": conv_id,
                "url": f"/chat?c={conv_id}",
                "tag": f"aird-chat-{conv_id}",
                "unread_total": total,
                "kind": "message",
            },
            expire_s=86400,
            push_ttl=86400,
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
    # Pins for the deleted message are removed in DB; refresh pin bars live.
    try:
        pins = chat_db.list_pins(username, conversation_id)
    except Exception:
        pins = []
    dispatch_pin(username, conversation_id, pins, pinned=False)


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


def dispatch_receipt(
    username: str,
    conversation_id: str,
    message_id: str,
    actor_id: int,
    *,
    decrypted: bool = False,
) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {
            "type": "chat_receipt",
            "conversation_id": conversation_id,
            "username": username,
            "last_read_id": message_id,
            "decrypted": decrypted,
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


def dispatch_chat_request(
    user_ids: list[int], conversation_id: str, *, actor: str = ""
) -> None:
    payload = {"type": "chat_request", "conversation_id": conversation_id}
    if actor:
        payload["from"] = actor
    get_chat_hub().broadcast_user_ids(user_ids, payload)


def announce_chat_request(
    actor: str, conversation_id: str, *, targets: list[str] | None = None
) -> None:
    """Tell every member a chat invite changed, and push people who still have to accept."""
    conv = chat_db.get_conversation(actor, conversation_id)
    if not conv:
        return
    members = list(conv.get("members") or [])
    pending = [m for m in members if not int(m.get("accepted", 1))]
    if targets is not None:
        wanted = {str(name) for name in targets if name and str(name) != actor}
        pending = [m for m in pending if str(m.get("username") or "") in wanted]
    if not pending:
        return
    user_ids = [int(m["user_id"]) for m in members if m.get("user_id") is not None]
    kind = conv.get("kind") or "dm"
    title = (conv.get("title") or "").strip() or "a group"
    preview = f"{actor} wants to chat." if kind == "dm" else f"{actor} added you to {title}."
    get_chat_hub().broadcast_user_ids(
        user_ids,
        {
            "type": "chat_request",
            "conversation_id": conversation_id,
            "from": actor,
            "preview": preview,
        },
    )
    from aird.notifications.engine import deliver

    for member in pending:
        username = str(member.get("username") or "")
        uid = member.get("user_id")
        if not username or username == actor or uid is None:
            continue
        try:
            deliver(
                username=username,
                user_id=int(uid),
                kind="chat_request",
                socket_payload=None,
                push_payload={
                    "title": actor or "Aird",
                    "body": preview,
                    "sender": actor,
                    "preview": preview,
                    "conversation_id": conversation_id,
                    "url": f"/chat?c={conversation_id}",
                    "tag": f"aird-chat-request-{conversation_id}",
                    "kind": "chat_request",
                },
            )
        except Exception:
            logger.debug("chat request push failed", exc_info=True)


def announce_enhanced_invite(
    actor: str, peer: str, peer_id: int, room_id: str, epk
) -> None:
    """Socket plus web push. The invite has to show up off the chat page too."""
    if not peer or peer == actor or not room_id or peer_id is None:
        return
    preview = f"{actor} wants to start an Enhanced Secure Chat."
    try:
        deliver(
            username=peer,
            user_id=int(peer_id),
            kind="enhanced_request",
            socket_payload={
                "type": "enhanced_request",
                "room_id": room_id,
                "from": actor,
                "epk": epk,
                "preview": preview,
            },
            push_payload={
                "title": actor or "Aird",
                "body": preview,
                "sender": actor,
                "preview": preview,
                "room_id": room_id,
                "url": "/chat",
                "tag": f"aird-enhanced-{room_id}",
                "kind": "enhanced_request",
            },
            expire_s=15 * 60,
            push_ttl=15 * 60,
        )
    except Exception:
        logger.debug("enhanced invite notify failed", exc_info=True)


def dispatch_e2e_key_ready(username: str, conversation_id: str) -> None:
    get_chat_hub().broadcast_user_ids(
        chat_db.member_user_ids(username, conversation_id),
        {"type": "chat_e2e_key_ready", "conversation_id": conversation_id},
    )
