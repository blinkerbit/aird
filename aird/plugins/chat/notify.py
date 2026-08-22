"""Push chat notifications after messages are persisted."""

from __future__ import annotations

from aird.plugins.chat import db as chat_db
from aird.plugins.chat.service import (
    notify_new_message,
    push_message_deleted,
    push_message_to_conversation,
)


def dispatch_message(conn, msg: dict, *, sender_id: int) -> None:
    recipient_id = chat_db.other_member_id(conn, int(msg["conversation_id"]), sender_id)
    push_message_to_conversation(conn, msg)
    if recipient_id is not None and recipient_id != sender_id:
        notify_new_message(conn, msg, recipient_id=recipient_id)


def dispatch_message_deleted(conn, conversation_id: int, message_id: int) -> None:
    push_message_deleted(conn, conversation_id, message_id)
