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
from aird.plugins.collab.reminders import is_reminders_enabled
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
            self.write(lists_db.list_index(conn, name, self.get_argument("q", "")))
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
                notes=str(body.get("notes") or ""),
            )
            if lst.get("id") and body.get("group_id"):
                lists_db.set_list_group(conn, str(lst["id"]), name, str(body.get("group_id")))
                lst = lists_db.list_detail(conn, str(lst["id"]), name) or lst
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
        except ValueError as err:
            self.set_status(400)
            self.write({"error": str(err)})
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
            from aird.db.user_tag_collab import tags_for_resource_display

            detail["tags"] = tags_for_resource_display(
                self.db_conn,
                str(detail.get("organizer") or ""),
                user_tags_db.KIND_LIST,
                list_id,
                name,
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
                notes=body.get("notes") if "notes" in body else None,
                allow_forward=bool(body.get("allow_forward")) if "allow_forward" in body else None,
                read_only=bool(body.get("read_only")) if "read_only" in body else None,
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

    @tornado.web.authenticated
    @require_db
    def delete(self, list_id: str):
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
            notify_changed(conn, "list", list_id)
            if not lists_db.delete_list(conn, list_id, name):
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            self.write({"ok": True})
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
            detail = lists_db.add_item(
                conn,
                list_id,
                name,
                str(body.get("text") or body.get("content") or ""),
                labels,
                notes=str(body.get("notes") or ""),
                due_at=body.get("due_at"),
                remind_at=body.get("remind_at") if is_reminders_enabled() else None,
                kind=body.get("type") if "type" in body else (body.get("kind") if "kind" in body else None),
            )
            if not detail:
                self.set_status(400)
                self.write({"error": "Could not add item"})
                return
            notify_changed(conn, "list", list_id)
            notify_event(conn, kind="list", artifact_id=list_id, actor=name, preview="Added an item")
            self.write(detail)
        except ValueError as err:
            self.set_status(400)
            self.write({"error": str(err)})
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
        done = body.get("done") if "done" in body else (body.get("isChecked") if "isChecked" in body else None)
        text = body.get("text") if "text" in body else (body.get("content") if "content" in body else None)
        labels = body.get("labels") if "labels" in body else (body.get("tags") if "tags" in body else None)
        if isinstance(labels, str):
            labels = [p.strip() for p in labels.split(",") if p.strip()]
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = lists_db.patch_item(
                conn,
                list_id,
                item_id,
                name,
                done=done,
                text=text,
                labels=labels,
                notes=body.get("notes") if "notes" in body else None,
                due_at=body.get("due_at") if "due_at" in body else None,
                remind_at=(
                    body.get("remind_at")
                    if is_reminders_enabled() and "remind_at" in body
                    else None
                ),
                due_set="due_at" in body,
                remind_set=is_reminders_enabled() and "remind_at" in body,
                sort_order=body.get("sort_order") if "sort_order" in body else None,
                kind=body.get("type") if "type" in body else (body.get("kind") if "kind" in body else None),
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
        except ValueError as err:
            self.set_status(400)
            self.write({"error": str(err)})
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


class ListSyncHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, list_id: str):
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
            from aird.plugins.collab import list_sync

            row = conn.execute("SELECT * FROM collab_lists WHERE id = ?", (list_id,)).fetchone()
            if not row or not lists_db._user_may_view(conn, row, name):
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            can_edit = lists_db._user_may_edit_items(conn, row, name)
            ops = body.get("ops") or [] if can_edit else []
            if not isinstance(ops, list):
                ops = []
            result = list_sync.sync_list(
                conn,
                list_id,
                actor_id=str(body.get("actor_id") or body.get("actorId") or name),
                since_hlc=str(body.get("since_hlc") or body.get("sinceHlc") or ""),
                ops=ops,
            )
            detail = lists_db.list_detail(conn, list_id, name)
            if ops:
                notify_changed(conn, "list", list_id)
            self.write({**result, "list": detail})
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
            if not detail or not lists_db.user_may_share(conn, list_id, name):
                self.set_status(403)
                self.write({"error": "Only the author can share this list"})
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


class ListGroupListHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
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
            group = lists_db.create_group(conn, name, str(body.get("name") or ""))
            if not group:
                self.set_status(400)
                self.write({"error": "Name required"})
                return
            self.write(group)
        finally:
            conn.close()


class ListGroupHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def patch(self, group_id: str):
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
            group = lists_db.update_group(
                conn,
                name,
                group_id,
                name=body.get("name") if "name" in body else None,
                sort_order=body.get("sort_order") if "sort_order" in body else None,
            )
            if not group:
                self.set_status(404)
                self.write({"error": "Not found"})
                return
            self.write(group)
        finally:
            conn.close()

    @tornado.web.authenticated
    @require_db
    def delete(self, group_id: str):
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
            if not lists_db.delete_group(conn, name, group_id):
                self.set_status(404)
                self.write({"error": "Not found"})
                return
            self.write({"ok": True})
        finally:
            conn.close()


class ListPlaceHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, list_id: str):
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
            if not lists_db.set_list_group(conn, list_id, name, str(body.get("group_id") or "")):
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            self.write({"ok": True, "group_id": str(body.get("group_id") or "")})
        finally:
            conn.close()


class ListOrderHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, list_id: str):
        if not _require(self):
            return
        self.check_xsrf_cookie()
        name = require_username(self)
        body = parse_json(self)
        ids = body.get("ids") or []
        if not isinstance(ids, list):
            self.set_status(400)
            self.write({"error": "ids required"})
            return
        conn = collab_conn()
        if conn is None:
            self.set_status(503)
            self.write({"error": "Database unavailable"})
            return
        try:
            detail = lists_db.reorder_items(conn, list_id, name, [str(item) for item in ids])
            if not detail:
                self.set_status(403)
                self.write({"error": "Forbidden"})
                return
            notify_changed(conn, "list", list_id)
            self.write(detail)
        finally:
            conn.close()
