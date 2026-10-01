"""WebSocket hub for mailbox chat."""

from __future__ import annotations

import json
import logging
import re

import tornado.ioloop
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
from aird.plugins.chat.secret import get_enhanced_secure_hub, grace_seconds
from aird.plugins.chat.service import get_chat_hub
from aird.utils.util import WebSocketConnectionManager

logger = logging.getLogger(__name__)
_ENHANCED_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,80}$")


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
        get_enhanced_secure_hub().clear_offline(username)
        self.write_message(json.dumps({"type": "chat_ready"}))
        for note in get_enhanced_secure_hub().pending_for(username):
            self.write_message(json.dumps(note))
        dispatch_presence(username, uid, True)

    def on_close(self):
        if self._user_id is not None and self._username:
            still = get_chat_hub().is_online(self._user_id)
            get_chat_hub().remove(self._user_id, self)
            if still and not get_chat_hub().is_online(self._user_id):
                dispatch_presence(self._username, self._user_id, False)
                self._arm_enhanced_secure_grace(self._username, self._user_id)
        super().on_close()

    def _arm_enhanced_secure_grace(self, username: str, user_id: int) -> None:
        token = get_enhanced_secure_hub().mark_offline(username)
        app = self.application

        def _fire() -> None:
            if get_enhanced_secure_hub().offline_token(username) != token:
                return
            if get_chat_hub().is_online(user_id):
                return
            ended = get_enhanced_secure_hub().drop_user(username)
            conn = app.settings.get("db_conn")
            for room_id, peer in ended:
                self._push_named(conn, peer, {
                    "type": "enhanced_ended",
                    "room_id": room_id,
                    "reason": "offline",
                })

        tornado.ioloop.IOLoop.current().call_later(grace_seconds(), _fire)

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
        elif msg_type == "enhanced_invite":
            self._handle_enhanced_invite(conn, data)
        elif msg_type == "enhanced_join":
            self._handle_enhanced_join(conn, data)
        elif msg_type == "enhanced_msg":
            self._handle_enhanced_msg(conn, data)
        elif msg_type == "enhanced_receipt":
            self._handle_enhanced_receipt(conn, data)
        elif msg_type == "enhanced_close":
            self._handle_enhanced_close(conn, data)

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
        decrypted = data.get("decrypted") is True
        if not conversation_id or not message_id:
            return
        if not chat_db.user_in_conversation(self._username, conversation_id):
            return
        chat_db.mark_read(
            actor=self._username,
            conversation_id=conversation_id,
            message_id=message_id,
            decrypted=decrypted,
        )
        dispatch_receipt(
            self._username,
            conversation_id,
            message_id,
            self._user_id,
            decrypted=decrypted,
        )

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

    def _enhanced_error(self, message: str) -> None:
        self.write_message(json.dumps({"type": "enhanced_error", "message": message}))

    def _push_named(self, conn, username: str, payload: dict) -> bool:
        if conn is None or not username:
            return False
        uid = chat_db.resolve_user_id(conn, username)
        if uid is None or not get_chat_hub().is_online(uid):
            return False
        get_chat_hub().send_to_user(uid, payload)
        return True

    def _user_online(self, conn, username: str) -> bool:
        uid = chat_db.resolve_user_id(conn, username) if conn is not None else None
        return uid is not None and get_chat_hub().is_online(uid)

    def _publish_enhanced_secure(self, conn, view: dict) -> None:
        if not view.get("ready"):
            return
        if not (self._user_online(conn, view["host"]) and self._user_online(conn, view["peer"])):
            return
        for name in (view["host"], view["peer"]):
            other = view["peer"] if name == view["host"] else view["host"]
            self._push_named(conn, name, {
                "type": "enhanced_open",
                "room_id": view["room_id"],
                "peer": other,
                "epk": view["pubs"].get(other),
            })

    def _handle_enhanced_invite(self, conn, data: dict) -> None:
        peer = str(data.get("peer") or "").strip()
        if not peer or peer == self._username:
            self._enhanced_error("Choose someone else")
            return
        if chat_db.resolve_user_id(conn, peer) is None:
            self._enhanced_error("No such user")
            return
        if not chat_db.find_dm_conversation(self._username, peer):
            self._enhanced_error("Start a normal chat with them first")
            return
        try:
            view = get_enhanced_secure_hub().invite(self._username, peer)
            if data.get("epk"):
                view = get_enhanced_secure_hub().join(
                    self._username, view["room_id"], data.get("epk")
                )
        except ValueError as exc:
            self._enhanced_error(str(exc))
            return
        self.write_message(json.dumps({
            "type": "enhanced_room",
            "room_id": view["room_id"],
            "peer": peer,
            "waiting": not view["ready"],
        }))
        if view["ready"]:
            self._publish_enhanced_secure(conn, view)
            return
        self._push_named(conn, peer, {
            "type": "enhanced_request",
            "room_id": view["room_id"],
            "from": self._username,
            "epk": view["pubs"].get(self._username),
        })

    def _handle_enhanced_join(self, conn, data: dict) -> None:
        room_id = str(data.get("room_id") or "")
        try:
            view = get_enhanced_secure_hub().join(
                self._username, room_id, data.get("epk")
            )
        except ValueError as exc:
            self._enhanced_error(str(exc))
            return
        if view["ready"]:
            self._publish_enhanced_secure(conn, view)
            return
        self.write_message(json.dumps({
            "type": "enhanced_room",
            "room_id": view["room_id"],
            "peer": view["peer"] if self._username == view["host"] else view["host"],
            "waiting": True,
        }))

    def _handle_enhanced_msg(self, conn, data: dict) -> None:
        room_id = str(data.get("room_id") or "")
        message_id = str(data.get("message_id") or "")
        if not _ENHANCED_MESSAGE_ID_RE.fullmatch(message_id):
            self._enhanced_error("Invalid Enhanced Secure Chat message")
            return
        try:
            peer, blob = get_enhanced_secure_hub().relay(
                self._username, room_id, data
            )
        except ValueError as exc:
            self._enhanced_error(str(exc))
            return
        if not self._user_online(conn, peer) or not self._user_online(conn, self._username):
            self._enhanced_error("Both people need to be online")
            return
        self._push_named(conn, peer, {
            "type": "enhanced_msg",
            "room_id": room_id,
            "message_id": message_id,
            "from": self._username,
            "iv": blob["iv"],
            "ct": blob["ct"],
        })

    def _handle_enhanced_receipt(self, conn, data: dict) -> None:
        room_id = str(data.get("room_id") or "")
        message_id = str(data.get("message_id") or "")
        status = str(data.get("status") or "")
        if status not in {"decrypted", "seen"}:
            return
        if not _ENHANCED_MESSAGE_ID_RE.fullmatch(message_id):
            return
        try:
            peer = get_enhanced_secure_hub().peer_for(self._username, room_id)
        except ValueError:
            return
        self._push_named(conn, peer, {
            "type": "enhanced_receipt",
            "room_id": room_id,
            "message_id": message_id,
            "status": status,
        })

    def _handle_enhanced_close(self, conn, data: dict) -> None:
        room_id = str(data.get("room_id") or "")
        view = get_enhanced_secure_hub().close(self._username, room_id)
        if view is None:
            return
        other = view["peer"] if self._username == view["host"] else view["host"]
        note = {"type": "enhanced_ended", "room_id": room_id, "reason": "closed"}
        self.write_message(json.dumps(note))
        self._push_named(conn, other, note)
