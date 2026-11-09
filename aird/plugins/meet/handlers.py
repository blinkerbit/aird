from __future__ import annotations

import uuid

import tornado.web

from aird.handlers.base_handler import BaseHandler, XSRFTokenMixin, require_db
from aird.plugins.access import PLUGIN_MEET, user_may_use_plugin
from aird.plugins.calls.config import livekit_api_key, livekit_api_secret, livekit_configured, livekit_url
from aird.plugins.calls.token import create_participant_token
from aird.plugins.chat import db as chat_db, is_chat_enabled
from aird.plugins.collab import meet_db
from aird.plugins.collab.chat_cards import share_card_to_conversation
from aird.plugins.collab.http import parse_json, require_username
from aird.plugins.collab.live import notify_changed, notify_event
from aird.plugins.collab.schema import collab_conn
from aird.plugins.meet import is_meet_enabled


def _require(handler: BaseHandler) -> bool:
    if not is_meet_enabled():
        handler.set_status(404)
        handler.write({"error": "Meet disabled"})
        return False
    name = require_username(handler)
    if not user_may_use_plugin(PLUGIN_MEET, name, handler.db_conn):
        handler.set_status(403)
        handler.write({"error": "Forbidden"})
        return False
    return True


class MeetPageHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require(self):
            return
        self.render("meet/index.html")


class MeetJoinPageHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self, slug: str):
        if not _require(self):
            return
        name = require_username(self)
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.finish("Database unavailable")
            return
        try:
            detail = meet_db.meet_detail_slug(conn, slug, name)
        finally:
            conn.close()
        if not detail:
            self.set_status(403)
            self.finish("Forbidden")
            return
        self.render("meet/join.html", meet=detail, slug=slug)


class MeetListHandler(BaseHandler, XSRFTokenMixin):
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
            self.write({"meets": meet_db.list_meets_for_user(conn, name)})
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
        title = str(body.get("title") or "").strip() or "Meeting"
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            meet = meet_db.create_meet(
                conn,
                organizer=name,
                title=title,
                visibility=str(body.get("visibility") or "public"),
                participants=body.get("participants"),
                slug=body.get("slug"),
            )
            from aird.db import user_tags as user_tags_db

            main = self.db_conn
            if main is not None and meet.get("id"):
                for raw_id in body.get("tag_ids") or []:
                    try:
                        user_tags_db.add_link(
                            main,
                            int(raw_id),
                            owner_username=name,
                            kind=user_tags_db.KIND_MEET,
                            resource_key=str(meet["id"]),
                        )
                    except (TypeError, ValueError):
                        continue
            self.write(meet)
        finally:
            conn.close()


class MeetDetailHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self, meet_id: str):
        if not _require(self):
            return
        name = require_username(self)
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = meet_db.meet_detail(conn, meet_id, name)
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            self.write(detail)
        finally:
            conn.close()

    @tornado.web.authenticated
    @require_db
    def patch(self, meet_id: str):
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
            detail = meet_db.update_meet(
                conn,
                meet_id,
                name,
                title=body.get("title") if "title" in body else None,
                visibility=body.get("visibility") if "visibility" in body else None,
                participants=body.get("participants") if "participants" in body else None,
                slug=body.get("slug") if "slug" in body else None,
            )
            if not detail:
                self.set_status(400)
                self.write({"error": "Could not update meeting"})
                return
            notify_changed(conn, "meet", meet_id)
            self.write(detail)
        finally:
            conn.close()

    @tornado.web.authenticated
    @require_db
    def delete(self, meet_id: str):
        if not _require(self):
            return
        self.check_xsrf_cookie()
        name = require_username(self)
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            if not meet_db.end_meet(conn, meet_id, name):
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            notify_changed(conn, "meet", meet_id)
            self.write({"ok": True})
        finally:
            conn.close()


class MeetTokenHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, meet_id: str):
        if not _require(self):
            return
        self.check_xsrf_cookie()
        if not livekit_configured():
            self.set_status(503)
            self.write({"error": "LiveKit not configured"})
            return
        name = require_username(self)
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            started = meet_db.claim_session_start(conn, meet_id, name)
            ok, reason = meet_db.meet_join_allowed(conn, meet_id, name)
            if not ok:
                self.set_status(403 if reason == "Forbidden" else 410)
                self.write({"error": reason})
                return
            if started:
                notify_changed(conn, "meet", meet_id)
                notify_event(
                    conn,
                    kind="meet",
                    artifact_id=meet_id,
                    actor=name,
                    preview="Meeting started",
                )
            row = conn.execute(
                "SELECT livekit_room FROM meets WHERE id = ?", (meet_id,)
            ).fetchone()
            room = str(row[0]) if row else f"aird-meet-{meet_id}"
        finally:
            conn.close()
        token = create_participant_token(
            api_key=livekit_api_key(),
            api_secret=livekit_api_secret(),
            room=room,
            identity=f"{name}:{uuid.uuid4().hex[:8]}",
            name=name,
        )
        self.write({"url": livekit_url(), "token": token, "room": room})


class MeetShareHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, meet_id: str):
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
            detail = meet_db.meet_detail(conn, meet_id, name)
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            snap = meet_db.meet_snapshot(conn, meet_id, name)
            msg = share_card_to_conversation(
                self.db_conn,
                username=name,
                user_id=uid,
                conversation_id=cid,
                kind="meet",
                artifact_id=meet_id,
                title=detail["title"],
                snapshot=snap,
                collab=conn,
            )
            self.write({"message": msg})
        finally:
            conn.close()
