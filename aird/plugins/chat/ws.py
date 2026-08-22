"""WebSocket hub for direct messages."""

from __future__ import annotations

import json
import logging

import tornado.websocket

from aird.core.security import is_valid_websocket_origin
from aird.handlers.base_handler import ManagedWebSocketMixin, authenticate_handler
from aird.plugins.chat import db as chat_db
from aird.plugins.chat import is_chat_enabled
from aird.plugins.chat.notify import dispatch_message
from aird.plugins.chat.sanitize import sanitize_chat_html
from aird.plugins.chat.service import get_chat_hub
from aird.utils.util import WebSocketConnectionManager

logger = logging.getLogger(__name__)


class ChatWebSocketHandler(ManagedWebSocketMixin, tornado.websocket.WebSocketHandler):
    connection_manager = WebSocketConnectionManager(
        "chat", default_max_connections=100, default_idle_timeout=600
    )

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self._user_id: int | None = None

    def check_origin(self, origin: str) -> bool:
        return is_valid_websocket_origin(self, origin)

    def open(self):
        if not is_chat_enabled():
            self.close(code=1008, reason="Chat disabled")
            return
        user = authenticate_handler(self)
        if not user:
            self.close(code=1008, reason="Authentication required")
            return
        username = user.get("username") if isinstance(user, dict) else None
        if not username:
            self.close(code=1008, reason="Authentication required")
            return
        conn = self.application.settings.get("db_conn")
        if conn is None:
            self.close(code=1011, reason="Database unavailable")
            return
        uid = chat_db.resolve_user_id(conn, username)
        if uid is None:
            self.close(code=1008, reason="Invalid user")
            return
        if not self.register_connection():
            return
        self._user_id = uid
        get_chat_hub().add(uid, self)
        self.write_message(json.dumps({"type": "chat_ready"}))

    def on_close(self):
        if self._user_id is not None:
            get_chat_hub().remove(self._user_id, self)
        super().on_close()

    def on_message(self, message):
        if self.reject_oversized_ws_message(message):
            return
        if not is_chat_enabled() or self._user_id is None:
            return
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            self.write_message(json.dumps({"type": "error", "message": "Invalid JSON"}))
            return
        msg_type = data.get("type")
        conn = self.application.settings.get("db_conn")
        if conn is None:
            return

        if msg_type == "chat_send":
            self._handle_send(conn, data)
        elif msg_type == "chat_read":
            self._handle_read(conn, data)
        elif msg_type == "chat_typing":
            self._handle_typing(conn, data)

    def _handle_send(self, conn, data: dict) -> None:
        conversation_id = int(data.get("conversation_id") or 0)
        if not chat_db.user_in_conversation(conn, conversation_id, self._user_id):
            self.write_message(json.dumps({"type": "error", "message": "Forbidden"}))
            return
        body = sanitize_chat_html(data.get("body") or "")
        if not body:
            self.write_message(json.dumps({"type": "error", "message": "Empty message"}))
            return
        msg = chat_db.insert_message(
            conn,
            conversation_id=conversation_id,
            sender_id=self._user_id,
            msg_type="text",
            body=body,
        )
        dispatch_message(conn, msg, sender_id=self._user_id)

    def _handle_read(self, conn, data: dict) -> None:
        conversation_id = int(data.get("conversation_id") or 0)
        message_id = int(data.get("message_id") or 0)
        if not chat_db.user_in_conversation(conn, conversation_id, self._user_id):
            return
        if message_id > 0:
            chat_db.mark_read(conn, conversation_id, self._user_id, message_id)

    def _handle_typing(self, conn, data: dict) -> None:
        conversation_id = int(data.get("conversation_id") or 0)
        if not chat_db.user_in_conversation(conn, conversation_id, self._user_id):
            return
        peer_id = chat_db.other_member_id(conn, conversation_id, self._user_id)
        if peer_id is None:
            return
        get_chat_hub().send_to_user(
            peer_id,
            {
                "type": "chat_typing",
                "conversation_id": conversation_id,
                "username": data.get("username"),
            },
        )
