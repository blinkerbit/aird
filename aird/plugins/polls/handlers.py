from __future__ import annotations

import tornado.web

from aird.handlers.base_handler import BaseHandler, XSRFTokenMixin, require_db
from aird.plugins.access import PLUGIN_POLLS, user_may_use_plugin
from aird.plugins.chat import db as chat_db, is_chat_enabled
from aird.plugins.collab import polls_db
from aird.plugins.collab.chat_cards import share_card_to_conversation
from aird.plugins.collab.http import parse_json, require_username
from aird.plugins.collab.live import notify_changed
from aird.plugins.collab.schema import collab_conn
from aird.plugins.polls import is_polls_enabled


def _require(handler: BaseHandler) -> bool:
    if not is_polls_enabled():
        handler.set_status(404)
        handler.write({"error": "Polls disabled"})
        return False
    name = require_username(handler)
    if not user_may_use_plugin(PLUGIN_POLLS, name, handler.db_conn):
        handler.set_status(403)
        handler.write({"error": "Forbidden"})
        return False
    return True


class PollPageHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require(self):
            return
        self.render("polls/index.html")


class PollListHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require(self):
            return
        name = require_username(self)
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            self.write({"polls": polls_db.list_polls_for_user(conn, name)})
        finally:
            conn.close()

    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require(self):
            return
        self.check_xsrf_cookie()
        name = require_username(self)
        body = parse_json(self)
        question = str(body.get("question") or "").strip()
        options = body.get("options") or []
        if not question or not isinstance(options, list) or len(options) < 2:
            self.set_status(400)
            self.write({"error": "Question and at least two options required"})
            return
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            poll = polls_db.create_poll(
                conn,
                organizer=name,
                question=question,
                visibility=str(body.get("visibility") or "public"),
                allow_multi=bool(body.get("allow_multi")),
                options=options,
                participants=body.get("participants"),
                slug=body.get("slug"),
            )
            self.write(poll)
        finally:
            conn.close()


class PollDetailHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self, poll_id: str):
        if not _require(self):
            return
        name = require_username(self)
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = polls_db.poll_detail(conn, poll_id, name)
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            self.write(detail)
        finally:
            conn.close()

    @tornado.web.authenticated
    @require_db
    def patch(self, poll_id: str):
        if not _require(self):
            return
        self.check_xsrf_cookie()
        name = require_username(self)
        body = parse_json(self)
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = polls_db.update_poll(
                conn,
                poll_id,
                name,
                question=body.get("question") if "question" in body else None,
                options=body.get("options") if "options" in body else None,
                visibility=body.get("visibility") if "visibility" in body else None,
                allow_multi=body.get("allow_multi") if "allow_multi" in body else None,
                participants=body.get("participants") if "participants" in body else None,
                slug=body.get("slug") if "slug" in body else None,
            )
            if not detail:
                self.set_status(400)
                self.write({"error": "Could not update poll"})
                return
            notify_changed(conn, "poll", poll_id)
            self.write(detail)
        finally:
            conn.close()


class PollVoteHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, poll_id: str):
        if not _require(self):
            return
        self.check_xsrf_cookie()
        name = require_username(self)
        body = parse_json(self)
        option_ids = body.get("option_ids") or body.get("option_id")
        if isinstance(option_ids, str):
            option_ids = [option_ids]
        if not isinstance(option_ids, list):
            self.set_status(400)
            self.write({"error": "option_ids required"})
            return
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = polls_db.cast_vote(conn, poll_id, name, [str(o) for o in option_ids])
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            notify_changed(conn, "poll", poll_id)
            self.write(detail)
        finally:
            conn.close()


class PollShareHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, poll_id: str):
        if not _require(self):
            return
        if not is_chat_enabled():
            self.set_status(503)
            self.write({"error": "Chat disabled"})
            return
        self.check_xsrf_cookie()
        name = require_username(self)
        uid = chat_db.resolve_user_id(self.db_conn, name)
        body = parse_json(self)
        cid = str(body.get("conversation_id") or "").strip()
        if not cid or not chat_db.user_in_conversation(name, cid):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = polls_db.poll_detail(conn, poll_id, name)
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            snap = polls_db.poll_snapshot(conn, poll_id, name)
            msg = share_card_to_conversation(
                self.db_conn,
                username=name,
                user_id=uid,
                conversation_id=cid,
                kind="poll",
                artifact_id=poll_id,
                title=detail["question"],
                snapshot=snap,
                collab=conn,
            )
            self.write({"message": msg})
        finally:
            conn.close()
