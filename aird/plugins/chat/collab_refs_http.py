"""Pin polls, lists, and meetings onto a conversation without posting a bubble."""

from __future__ import annotations

import tornado.web

from aird.handlers.base_handler import BaseHandler, XSRFTokenMixin, require_db
from aird.plugins.chat import db as chat_db
from aird.plugins.chat.handlers import _require_chat, _username
from aird.plugins.collab.live import notify_changed
from aird.plugins.collab.refs import link_ref, list_refs, suggest
from aird.plugins.collab.schema import collab_conn

_KINDS = {"poll": "polls", "list": "lists", "meet": "meet"}


def _kind_enabled(kind: str) -> bool:
    if kind == "poll":
        from aird.plugins.polls import is_polls_enabled
        return is_polls_enabled()
    if kind == "list":
        from aird.plugins.lists import is_lists_enabled
        return is_lists_enabled()
    if kind == "meet":
        from aird.plugins.meet import is_meet_enabled
        return is_meet_enabled()
    return False


class ChatConversationRefsHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self, conversation_id: str):
        if not _require_chat(self):
            return
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            self.write({"refs": list_refs(conn, conversation_id, name)})
        finally:
            conn.close()

    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        body = self.parse_json_body()
        kind = str(body.get("kind") or "").strip().lower()
        if kind not in _KINDS or not _kind_enabled(kind):
            self.set_status(400)
            self.write({"error": "Unknown kind"})
            return
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            members = [m.get("username") for m in chat_db.members_of(name, conversation_id)]
            ref = link_ref(
                conn,
                conversation_id=conversation_id,
                kind=kind,
                username=name,
                artifact_id=str(body.get("id") or ""),
                slug=str(body.get("slug") or ""),
                member_names=[m for m in members if m],
            )
            if not ref:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            notify_changed(conn, kind, str(ref.get("id") or ""))
            self.write({"ref": ref})
        finally:
            conn.close()


class ChatCollabSuggestHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        name = _username(self)
        kind = str(self.get_argument("kind", "")).strip().lower()
        if kind not in _KINDS or not _kind_enabled(kind):
            self.write({"items": []})
            return
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            self.write({"items": suggest(conn, name, kind, self.get_argument("q", ""))})
        finally:
            conn.close()
