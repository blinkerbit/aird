"""WebSocket hub for mailbox chat."""

from __future__ import annotations

import json
import logging

import tornado.websocket

from aird.core.security import is_valid_websocket_origin
from aird.handlers.base_handler import ManagedWebSocketMixin, authenticate_handler
from aird.plugins.chat import db as chat_db
from aird.plugins.chat import is_chat_enabled
from aird.plugins.chat.e2e import metadata_from_e2e_request
from aird.plugins.chat.notify import (
    dispatch_message,
    dispatch_presence,
    dispatch_receipt,
    dispatch_typing,
    dispatch_e2e_need_key,
)
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
        self._username: str | None = None

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
        from aird.plugins.access import PLUGIN_CHAT, user_may_use_plugin

        if not user_may_use_plugin(PLUGIN_CHAT, username, conn):
            self.close(code=1008, reason="Chat not assigned")
            return
        uid = chat_db.resolve_user_id(conn, username)
        if uid is None:
            self.close(code=1008, reason="Invalid user")
            return
        if not self.register_connection():
            return
        self._user_id = uid
        self._username = username
        chat_db.maybe_migrate_user(conn, username)
        get_chat_hub().add(uid, self, username)
        self.write_message(json.dumps({"type": "chat_ready"}))
        dispatch_presence(username, uid, True)

    def on_close(self):
        if self._user_id is not None and self._username:
            still = get_chat_hub().is_online(self._user_id)
            get_chat_hub().remove(self._user_id, self)
            if still and not get_chat_hub().is_online(self._user_id):
                dispatch_presence(self._username, self._user_id, False)
        super().on_close()

    def on_message(self, message):
        if self.reject_oversized_ws_message(message):
            return
        if not is_chat_enabled() or self._user_id is None or not self._username:
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
            self._handle_read(data)
        elif msg_type == "chat_typing":
            self._handle_typing(data)
        elif msg_type == "chat_e2e_need_key":
            self._handle_e2e_need_key(data)

    def _handle_send(self, conn, data: dict) -> None:
        conversation_id = str(data.get("conversation_id") or "")
        if not conversation_id or not chat_db.user_in_conversation(self._username, conversation_id):
            self.write_message(json.dumps({"type": "error", "message": "Forbidden"}))
            return
        reply_to = str(data.get("reply_to_id") or "") or None
        try:
            if data.get("e2e"):
                meta = metadata_from_e2e_request(data)
                msg = chat_db.insert_message(
                    conn,
                    username=self._username,
                    conversation_id=conversation_id,
                    msg_type="text",
                    body="",
                    metadata=meta,
                    reply_to_id=reply_to,
                )
            else:
                body = sanitize_chat_html(data.get("body") or "")
                if not body:
                    self.write_message(json.dumps({"type": "error", "message": "Empty message"}))
                    return
                msg = chat_db.insert_message(
                    conn,
                    username=self._username,
                    conversation_id=conversation_id,
                    msg_type="text",
                    body=body,
                    reply_to_id=reply_to,
                )
        except ValueError as exc:
            self.write_message(json.dumps({"type": "error", "message": str(exc)}))
            return
        dispatch_message(msg, sender_id=self._user_id, sender_username=self._username)

    def _handle_read(self, data: dict) -> None:
        conversation_id = str(data.get("conversation_id") or "")
        message_id = str(data.get("message_id") or "")
        if not conversation_id or not message_id:
            return
        if not chat_db.user_in_conversation(self._username, conversation_id):
            return
        chat_db.mark_read(actor=self._username, conversation_id=conversation_id, message_id=message_id)
        dispatch_receipt(self._username, conversation_id, message_id, self._user_id)

    def _handle_typing(self, data: dict) -> None:
        conversation_id = str(data.get("conversation_id") or "")
        if not conversation_id or not chat_db.user_in_conversation(self._username, conversation_id):
            return
        dispatch_typing(self._username, conversation_id, self._user_id)

    def _handle_e2e_need_key(self, data: dict) -> None:
        conversation_id = str(data.get("conversation_id") or "")
        if not conversation_id or not chat_db.user_in_conversation(self._username, conversation_id):
            return
        dispatch_e2e_need_key(self._username, conversation_id, self._user_id)
