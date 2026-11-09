"""Per-user tag API and management page."""

from __future__ import annotations

import logging

import tornado.web

from aird.db import user_tags as user_tags_db
from aird.db.user_tags import MemberValidationError, normalize_members
from aird.db.user_tags import list_granted_files
from aird.handlers.base_handler import (
    BaseHandler,
    XSRFTokenMixin,
    get_username_string_for_db,
    require_db,
)
from aird.utils.tag_display import tag_chip_inline_style

logger = logging.getLogger(__name__)
CONTENT_TYPE_JSON = "application/json"


class UserTagsPageHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self) -> None:
        self.render(
            "user_tags.html",
            nav_active="tags",
            tag_chip_style=tag_chip_inline_style,
        )


class UserTagsAPIHandler(XSRFTokenMixin, BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self) -> None:
        name = get_username_string_for_db(self) or ""
        tags = user_tags_db.list_tags_for_owner(self.db_conn, name)
        for tag in tags:
            tag["display_name"] = user_tags_db.format_tag_display(
                tag["name"], tag["owner_username"], name
            )
        granted = list_granted_files(self.db_conn, name)
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({"tags": tags, "granted_files": granted})

    @tornado.web.authenticated
    @require_db
    def post(self) -> None:
        self.check_xsrf_cookie()
        name = get_username_string_for_db(self) or ""
        payload = self.parse_json_body() or {}
        tag_name = str(payload.get("name") or "").strip()
        if not tag_name or len(tag_name) > 64:
            self.set_status(400)
            self.write({"error": "Invalid tag name"})
            return
        members = payload.get("members")
        if members is not None and not isinstance(members, list):
            self.set_status(400)
            self.write({"error": "members must be a list"})
            return
        try:
            if members is not None:
                normalize_members(self.db_conn, name, members)
        except MemberValidationError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        created = user_tags_db.create_tag(
            self.db_conn,
            owner_username=name,
            name=tag_name,
            access=str(payload.get("access") or "none"),
            color=payload.get("color"),
            members=members,
        )
        if not created:
            self.set_status(409)
            self.write({"error": "Tag already exists, invalid members, or could not be created"})
            return
        self.set_status(201)
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write(created)


class UserTagDetailAPIHandler(XSRFTokenMixin, BaseHandler):
    @tornado.web.authenticated
    @require_db
    def patch(self, tag_id: str) -> None:
        self.check_xsrf_cookie()
        name = get_username_string_for_db(self) or ""
        try:
            tid = int(tag_id)
        except (TypeError, ValueError):
            self.set_status(404)
            self.write({"error": "Not found"})
            return
        payload = self.parse_json_body() or {}
        members = payload.get("members") if "members" in payload else None
        if members is not None and not isinstance(members, list):
            self.set_status(400)
            self.write({"error": "members must be a list"})
            return
        try:
            if members is not None:
                normalize_members(self.db_conn, name, members)
        except MemberValidationError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        updated = user_tags_db.update_tag(
            self.db_conn,
            tid,
            owner_username=name,
            name=payload.get("name") if "name" in payload else None,
            access=payload.get("access") if "access" in payload else None,
            color=payload.get("color") if "color" in payload else None,
            members=members,
        )
        if not updated:
            self.set_status(404)
            self.write({"error": "Not found or conflict"})
            return
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write(updated)

    @tornado.web.authenticated
    @require_db
    def delete(self, tag_id: str) -> None:
        self.check_xsrf_cookie()
        name = get_username_string_for_db(self) or ""
        try:
            tid = int(tag_id)
        except (TypeError, ValueError):
            self.set_status(404)
            self.write({"error": "Not found"})
            return
        if not user_tags_db.delete_tag(self.db_conn, tid, owner_username=name):
            self.set_status(404)
            self.write({"error": "Not found"})
            return
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({"ok": True})


class UserTagLinksAPIHandler(XSRFTokenMixin, BaseHandler):
    @tornado.web.authenticated
    @require_db
    def post(self, tag_id: str) -> None:
        self.check_xsrf_cookie()
        name = get_username_string_for_db(self) or ""
        try:
            tid = int(tag_id)
        except (TypeError, ValueError):
            self.set_status(400)
            self.write({"error": "Invalid tag id"})
            return
        payload = self.parse_json_body() or {}
        kind = str(payload.get("kind") or "").strip().lower()
        resource_key = str(payload.get("resource_key") or "").strip()
        if not kind or not resource_key:
            self.set_status(400)
            self.write({"error": "kind and resource_key required"})
            return
        if not user_tags_db.add_link(
            self.db_conn,
            tid,
            owner_username=name,
            kind=kind,
            resource_key=resource_key,
        ):
            self.set_status(403)
            self.write({"error": "Forbidden or invalid link"})
            return
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({"ok": True})

    @tornado.web.authenticated
    @require_db
    def delete(self, tag_id: str) -> None:
        self.check_xsrf_cookie()
        name = get_username_string_for_db(self) or ""
        try:
            tid = int(tag_id)
        except (TypeError, ValueError):
            self.set_status(400)
            self.write({"error": "Invalid tag id"})
            return
        payload = self.parse_json_body() or {}
        kind = str(payload.get("kind") or "").strip().lower()
        resource_key = str(payload.get("resource_key") or "").strip()
        if not user_tags_db.remove_link(
            self.db_conn,
            tid,
            owner_username=name,
            kind=kind,
            resource_key=resource_key,
        ):
            self.set_status(404)
            self.write({"error": "Not found"})
            return
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({"ok": True})


class UserTagBrowseCatalogAPIHandler(XSRFTokenMixin, BaseHandler):
    """Tag names and colors for the current user's browse UI."""

    @tornado.web.authenticated
    @require_db
    def get(self) -> None:
        name = get_username_string_for_db(self) or ""
        tags = user_tags_db.list_tags_for_owner(self.db_conn, name)
        colors = {str(t["name"]): t["color"] for t in tags}
        names = sorted(colors.keys())
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({"names": names, "colors": colors, "tags": tags})
