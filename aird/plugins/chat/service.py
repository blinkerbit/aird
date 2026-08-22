"""Online user tracking and chat message dispatch."""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

import tornado.ioloop

from aird.plugins.chat import db as chat_db
from aird.plugins.chat.sanitize import plain_preview

if TYPE_CHECKING:
    from aird.plugins.chat.ws import ChatWebSocketHandler

logger = logging.getLogger(__name__)


class ChatHub:
    """Track WebSocket connections per user id (in-process)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_user: dict[int, set[ChatWebSocketHandler]] = {}

    def add(self, user_id: int, handler: ChatWebSocketHandler) -> None:
        with self._lock:
            self._by_user.setdefault(user_id, set()).add(handler)

    def remove(self, user_id: int, handler: ChatWebSocketHandler) -> None:
        with self._lock:
            bucket = self._by_user.get(user_id)
            if not bucket:
                return
            bucket.discard(handler)
            if not bucket:
                self._by_user.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        with self._lock:
            return bool(self._by_user.get(user_id))

    def _schedule_send(self, handler: ChatWebSocketHandler, raw: str) -> None:
        loop = tornado.ioloop.IOLoop.current(instance=False)
        if loop is None:

            def _direct() -> None:
                try:
                    handler.write_message(raw)
                except Exception:
                    logger.debug("chat ws send failed", exc_info=True)

            _direct()
            return

        def _write() -> None:
            try:
                handler.write_message(raw)
            except Exception:
                logger.debug("chat ws send failed", exc_info=True)

        try:
            loop.add_callback(_write)
        except RuntimeError:
            _write()

    def send_to_user(self, user_id: int, payload: dict) -> None:
        with self._lock:
            targets = list(self._by_user.get(user_id, ()))
        raw = json.dumps(payload)
        for handler in targets:
            self._schedule_send(handler, raw)

    def broadcast_conversation(
        self,
        conn,
        conversation_id: int,
        payload: dict,
        *,
        exclude_user_id: int | None = None,
    ) -> None:
        rows = conn.execute(
            "SELECT user_id FROM chat_members WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        for (uid,) in rows:
            if exclude_user_id is not None and uid == exclude_user_id:
                continue
            self.send_to_user(int(uid), payload)


_chat_hub = ChatHub()


def get_chat_hub() -> ChatHub:
    return _chat_hub


def message_preview(msg: dict) -> str:
    msg_type = msg.get("msg_type")
    if msg_type == "text":
        return plain_preview(msg.get("body") or "")
    if msg_type == "file":
        return "Shared a file"
    if msg_type == "gif":
        return "Sent a GIF"
    return "New message"


def notify_new_message(conn, msg: dict, *, recipient_id: int) -> None:
    unread = chat_db.unread_summary(conn, recipient_id)
    preview = message_preview(msg)
    get_chat_hub().send_to_user(
        recipient_id,
        {
            "type": "chat_notify",
            "conversation_id": msg["conversation_id"],
            "message_id": msg["id"],
            "sender": msg.get("sender_username"),
            "msg_type": msg.get("msg_type"),
            "preview": preview,
            "unread_total": unread["total"],
        },
    )


def push_message_to_conversation(conn, msg: dict) -> None:
    payload = {"type": "chat_message", "message": msg}
    get_chat_hub().broadcast_conversation(
        conn,
        int(msg["conversation_id"]),
        payload,
    )


def push_message_deleted(conn, conversation_id: int, message_id: int) -> None:
    payload = {
        "type": "chat_message_deleted",
        "conversation_id": conversation_id,
        "message_id": message_id,
    }
    get_chat_hub().broadcast_conversation(conn, conversation_id, payload)
