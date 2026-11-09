from __future__ import annotations

import tornado.web

from aird.handlers.base_handler import BaseHandler, XSRFTokenMixin, require_db
from aird.plugins.access import PLUGIN_LISTS, user_may_use_plugin
from aird.plugins.chat import db as chat_db, is_chat_enabled
from aird.plugins.collab import lists_db
from aird.plugins.collab.chat_cards import share_card_to_conversation
from aird.plugins.collab.http import parse_json, require_username
from aird.plugins.collab.live import notify_changed, notify_event
from aird.plugins.collab.schema import collab_conn
from aird.plugins.lists import is_lists_enabled


def _require(handler: BaseHandler) -> bool:
    if not is_lists_enabled():
        handler.set_status(404)
        handler.write({"error": "Lists disabled"})
        return False
    name = require_username(handler)
    if not user_may_use_plugin(PLUGIN_LISTS, name, handler.db_conn):
        handler.set_status(403)
        handler.write({"error": "Forbidden"})
        return False
    return True


class ListPageHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require(self):
            return
        self.render("lists/index.html")


class ListListHandler(BaseHandler, XSRFTokenMixin):
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
            self.write({"lists": lists_db.list_lists_for_user(conn, name)})
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
        title = str(body.get("title") or "").strip()
        if not title:
            self.set_status(400)
            self.write({"error": "Title required"})
            return
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            lst = lists_db.create_list(
                conn,
                organizer=name,
                title=title,
                kind=str(body.get("kind") or "todo"),
                visibility=str(body.get("visibility") or "public"),
                items=body.get("items") or [],
                participants=body.get("participants"),
                slug=body.get("slug"),
            )
            from aird.db import user_tags as user_tags_db
            from aird.db.user_tag_collab import augment_detail_tag_ids, replace_artifact_tag_links

            main = self.db_conn
            if main is not None and lst.get("id") and "tag_ids" in body:
                replace_artifact_tag_links(
                    main,
                    owner_username=name,
                    kind=user_tags_db.KIND_LIST,
                    resource_key=str(lst["id"]),
                    tag_ids=body.get("tag_ids"),
                )
            lst = augment_detail_tag_ids(
                main, lst, viewer=name, kind=user_tags_db.KIND_LIST
            ) or lst
            self.write(lst)
        finally:
            conn.close()


class ListDetailHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self, list_id: str):
        if not _require(self):
            return
        name = require_username(self)
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = lists_db.list_detail(conn, list_id, name)
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            from aird.db import user_tags as user_tags_db
            from aird.db.user_tag_collab import augment_detail_tag_ids

            detail = augment_detail_tag_ids(
                self.db_conn, detail, viewer=name, kind=user_tags_db.KIND_LIST
            )
            self.write(detail)
        finally:
            conn.close()

    @tornado.web.authenticated
    @require_db
    def patch(self, list_id: str):
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
            detail = lists_db.update_list(
                conn,
                list_id,
                name,
                title=body.get("title") if "title" in body else None,
                kind=body.get("kind") if "kind" in body else None,
                visibility=body.get("visibility") if "visibility" in body else None,
                participants=body.get("participants") if "participants" in body else None,
                slug=body.get("slug") if "slug" in body else None,
            )
            if not detail:
                self.set_status(400)
                self.write({"error": "Could not update list"})
                return
            from aird.db import user_tags as user_tags_db
            from aird.db.user_tag_collab import augment_detail_tag_ids, replace_artifact_tag_links

            main = self.db_conn
            if main is not None and "tag_ids" in body:
                replace_artifact_tag_links(
                    main,
                    owner_username=name,
                    kind=user_tags_db.KIND_LIST,
                    resource_key=list_id,
                    tag_ids=body.get("tag_ids"),
                )
            detail = augment_detail_tag_ids(
                main, detail, viewer=name, kind=user_tags_db.KIND_LIST
            )
            notify_changed(conn, "list", list_id)
            notify_event(conn, kind="list", artifact_id=list_id, actor=name, preview="Updated the list")
            self.write(detail)
        finally:
            conn.close()


class ListItemsHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, list_id: str):
        if not _require(self):
            return
        self.check_xsrf_cookie()
        name = require_username(self)
        body = parse_json(self)
        labels = body.get("labels") or []
        if isinstance(labels, str):
            labels = [p.strip() for p in labels.split(",") if p.strip()]
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = lists_db.add_item(conn, list_id, name, str(body.get("text") or ""), labels)
            if not detail:
                self.set_status(400)
                self.write({"error": "Could not add item"})
                return
            notify_changed(conn, "list", list_id)
            notify_event(conn, kind="list", artifact_id=list_id, actor=name, preview="Added an item")
            self.write(detail)
        finally:
            conn.close()


class ListItemHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def patch(self, list_id: str, item_id: str):
        if not _require(self):
            return
        self.check_xsrf_cookie()
        name = require_username(self)
        body = parse_json(self)
        done = body.get("done") if "done" in body else None
        text = body.get("text") if "text" in body else None
        labels = body.get("labels") if "labels" in body else None
        if isinstance(labels, str):
            labels = [p.strip() for p in labels.split(",") if p.strip()]
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = lists_db.patch_item(
                conn, list_id, item_id, name, done=done, text=text, labels=labels
            )
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            notify_changed(conn, "list", list_id)
            if "done" in body and "text" not in body and "labels" not in body:
                preview = "Checked an item" if body.get("done") else "Unchecked an item"
            else:
                preview = "Updated an item"
            notify_event(conn, kind="list", artifact_id=list_id, actor=name, preview=preview)
            self.write(detail)
        finally:
            conn.close()

    @tornado.web.authenticated
    @require_db
    def delete(self, list_id: str, item_id: str):
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
            detail = lists_db.delete_item(conn, list_id, item_id, name)
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            notify_changed(conn, "list", list_id)
            notify_event(conn, kind="list", artifact_id=list_id, actor=name, preview="Removed an item")
            self.write(detail)
        finally:
            conn.close()


class ListShareHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, list_id: str):
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
            detail = lists_db.list_detail(conn, list_id, name)
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            snap = lists_db.list_snapshot(conn, list_id, name)
            msg = share_card_to_conversation(
                self.db_conn,
                username=name,
                user_id=uid,
                conversation_id=cid,
                kind="list",
                artifact_id=list_id,
                title=detail["title"],
                snapshot=snap,
                collab=conn,
            )
            self.write({"message": msg})
        finally:
            conn.close()
