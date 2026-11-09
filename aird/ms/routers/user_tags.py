"""Per-user tag API and page (Starlette)."""

from __future__ import annotations

import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from aird.db import user_tags as user_tags_db
from aird.db.user_tags import MemberValidationError, normalize_members
from aird.ms.ctx import check_xsrf_token
from aird.ms.deps import db_conn, require_user, username_from_user
from aird.ms.render import render_html
from aird.utils.tag_display import tag_chip_inline_style

logger = logging.getLogger(__name__)


def _username(request: Request) -> str:
    user = require_user(request)
    return username_from_user(user) if isinstance(user, dict) else ""


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def user_tags_page(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return RedirectResponse("/login", status_code=302)
    return render_html(
        request,
        "user_tags.html",
        nav_active="tags",
        tag_chip_style=tag_chip_inline_style,
    )


async def user_tags_api(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = db_conn(request)
    if conn is None:
        return JSONResponse({"error": "Database unavailable"}, status_code=500)
    name = _username(request)
    if request.method == "GET":
        tags = user_tags_db.list_tags_for_owner(conn, name)
        for tag in tags:
            tag["display_name"] = user_tags_db.format_tag_display(
                tag["name"], tag["owner_username"], name
            )
        granted = user_tags_db.list_granted_files(conn, name)
        return JSONResponse({"tags": tags, "granted_files": granted})
    payload = await _json_body(request)
    try:
        check_xsrf_token(request, payload.get("_xsrf"))
    except PermissionError:
        return JSONResponse({"error": "XSRF validation failed"}, status_code=403)
    tag_name = str(payload.get("name") or "").strip()
    if not tag_name or len(tag_name) > 64:
        return JSONResponse({"error": "Invalid tag name"}, status_code=400)
    members = payload.get("members")
    if members is not None and not isinstance(members, list):
        return JSONResponse({"error": "members must be a list"}, status_code=400)
    try:
        if members is not None:
            normalize_members(conn, name, members)
    except MemberValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    created = user_tags_db.create_tag(
        conn,
        owner_username=name,
        name=tag_name,
        access=str(payload.get("access") or "none"),
        color=payload.get("color"),
        members=members,
    )
    if not created:
        return JSONResponse({"error": "Tag already exists or could not be created"}, status_code=409)
    return JSONResponse(created, status_code=201)


async def user_tags_browse_catalog(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = db_conn(request)
    if conn is None:
        return JSONResponse({"error": "Database unavailable"}, status_code=500)
    name = _username(request)
    tags = user_tags_db.list_tags_for_owner(conn, name)
    colors = {str(t["name"]): t["color"] for t in tags}
    names = sorted(colors.keys())
    return JSONResponse({"names": names, "colors": colors, "tags": tags})


async def user_tag_detail(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = db_conn(request)
    if conn is None:
        return JSONResponse({"error": "Database unavailable"}, status_code=500)
    name = _username(request)
    try:
        tid = int(request.path_params["tag_id"])
    except (TypeError, ValueError, KeyError):
        return JSONResponse({"error": "Not found"}, status_code=404)
    payload = await _json_body(request)
    if request.method == "DELETE":
        try:
            check_xsrf_token(request, payload.get("_xsrf"))
        except PermissionError:
            return JSONResponse({"error": "XSRF validation failed"}, status_code=403)
        if not user_tags_db.delete_tag(conn, tid, owner_username=name):
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse({"ok": True})
    try:
        check_xsrf_token(request, payload.get("_xsrf"))
    except PermissionError:
        return JSONResponse({"error": "XSRF validation failed"}, status_code=403)
    members = payload.get("members") if "members" in payload else None
    if members is not None and not isinstance(members, list):
        return JSONResponse({"error": "members must be a list"}, status_code=400)
    try:
        if members is not None:
            normalize_members(conn, name, members)
    except MemberValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    updated = user_tags_db.update_tag(
        conn,
        tid,
        owner_username=name,
        name=payload.get("name") if "name" in payload else None,
        access=payload.get("access") if "access" in payload else None,
        color=payload.get("color") if "color" in payload else None,
        members=members,
    )
    if not updated:
        return JSONResponse({"error": "Not found or conflict"}, status_code=404)
    return JSONResponse(updated)


async def user_tag_links(request: Request) -> Response:
    user = require_user(request)
    if not isinstance(user, dict):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = db_conn(request)
    if conn is None:
        return JSONResponse({"error": "Database unavailable"}, status_code=500)
    name = _username(request)
    try:
        tid = int(request.path_params["tag_id"])
    except (TypeError, ValueError, KeyError):
        return JSONResponse({"error": "Invalid tag id"}, status_code=400)
    payload = await _json_body(request)
    try:
        check_xsrf_token(request, payload.get("_xsrf"))
    except PermissionError:
        return JSONResponse({"error": "XSRF validation failed"}, status_code=403)
    kind = str(payload.get("kind") or "").strip().lower()
    resource_key = str(payload.get("resource_key") or "").strip()
    if request.method == "POST":
        if not kind or not resource_key:
            return JSONResponse({"error": "kind and resource_key required"}, status_code=400)
        if not user_tags_db.add_link(
            conn,
            tid,
            owner_username=name,
            kind=kind,
            resource_key=resource_key,
        ):
            return JSONResponse({"error": "Forbidden or invalid link"}, status_code=403)
        return JSONResponse({"ok": True})
    if not user_tags_db.remove_link(
        conn,
        tid,
        owner_username=name,
        kind=kind,
        resource_key=resource_key,
    ):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})


routes = [
    Route("/tags", user_tags_page, methods=["GET"]),
    Route("/api/user-tags", user_tags_api, methods=["GET", "POST"]),
    Route("/api/user-tags/browse-catalog", user_tags_browse_catalog, methods=["GET"]),
    Route("/api/user-tags/{tag_id}", user_tag_detail, methods=["PATCH", "DELETE"]),
    Route("/api/user-tags/{tag_id}/links", user_tag_links, methods=["POST", "DELETE"]),
]

__all__ = ["routes"]
