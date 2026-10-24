"""HTTP: mint LiveKit tokens for chat members."""

from __future__ import annotations

import json
import logging
import uuid

import tornado.web

from aird.handlers.base_handler import BaseHandler, XSRFTokenMixin, get_username_string_for_db, require_db
from aird.plugins.access import PLUGIN_CALLS, user_may_use_plugin
from aird.plugins.calls import is_calls_enabled
from aird.plugins.calls.config import (
    livekit_api_key,
    livekit_api_secret,
    livekit_url,
    room_name,
)
from aird.plugins.calls.notify import dispatch_call_invite, read_call_ring
from aird.plugins.calls.token import create_participant_token
from aird.plugins.chat import db as chat_db
from aird.plugins.chat import is_chat_enabled

logger = logging.getLogger(__name__)


def _require_calls(handler: BaseHandler) -> bool:
    if not is_chat_enabled():
        handler.set_status(404)
        handler.write({"error": "Chat disabled"})
        return False
    if not is_calls_enabled():
        handler.set_status(503)
        handler.write({"error": "Voice and video calls are not configured"})
        return False
    username = get_username_string_for_db(handler)
    if not username or not user_may_use_plugin(PLUGIN_CALLS, username, handler.db_conn):
        handler.set_status(403)
        handler.write({"error": "Calls not available"})
        return False
    return True


class ChatCallHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_calls(self):
            return
        username = get_username_string_for_db(self)
        if not username:
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        if not chat_db.user_in_conversation(username, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            body = {}
        mode = str(body.get("mode") or "audio").lower()
        if mode not in ("audio", "video"):
            mode = "audio"
        uid = chat_db.resolve_user_id(self.db_conn, username)
        if uid is None:
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        room = room_name(conversation_id)
        call_id = str(body.get("call_id") or "").strip() or uuid.uuid4().hex
        token = create_participant_token(
            api_key=livekit_api_key(),
            api_secret=livekit_api_secret(),
            room=room,
            identity=f"{username}:{uuid.uuid4().hex[:8]}",
            name=username,
        )
        if body.get("invite") is not False:
            dispatch_call_invite(
                conversation_id=conversation_id,
                caller_username=username,
                caller_id=uid,
                mode=mode,
                room=room,
                call_id=call_id,
            )
        self.write(
            {
                "url": livekit_url(),
                "token": token,
                "room": room,
                "mode": mode,
                "call_id": call_id,
            }
        )


class CallRingHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_calls(self):
            return
        username = get_username_string_for_db(self)
        ring = read_call_ring(self.db_conn, username) if username else None
        self.write({"ring": ring})
