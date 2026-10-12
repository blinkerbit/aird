"""Share UI and API routes (Starlette)."""

from __future__ import annotations

import json
import logging
import os
import posixpath
import secrets
import threading
import time
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from aird.constants.input_limits import (
    ACCESS_TOKEN_MAX_LEN,
    REL_PATH_MAX_LEN,
    SHARE_ID_MAX_LEN,
    SHARE_JSON_BODY_MAX_BYTES,
)
from aird.core.input_validation import validate_share_create_struct
from aird.core.browse_paths import resolve_share_rel
from aird.core.security import is_within_root
from aird.domain.contracts import ShareCreateRequest, ShareCreateResponse
from aird.handlers.base_handler import login_matches_share_creator_field
from aird.handlers.constants import FS_DISABLED_MSG, INVALID_SHARE_LINK
from aird.handlers.share_handlers import (
    _check_share_access,
    _create_share_record,
    _get_share_file_list,
    _is_path_in_share,
    _is_user_allowed_for_modify,
    _root_dir_for_share,
    _resolve_share_paths,
)
from aird.ms.ctx import check_xsrf_token, user_root_for
from aird.ms.deps import db_conn, get_service, is_admin_user, require_user, username_from_user
from aird.ms.render import render_html
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)
_TOKEN_ATTEMPTS: dict[str, tuple[int, float]] = {}
_TOKEN_ATTEMPTS_LOCK = threading.Lock()


class _ShareAdapter:
    """Minimal stand-in for BaseHandler share helpers."""

    def __init__(self, request: Request, user):
        self.request = request
        self._user = user

    def get_service(self, name: str):
        return get_service(self.request, name)

    def get_display_username(self) -> str:
        return username_from_user(self._user) or ""

    def is_admin_user(self) -> bool:
        return is_admin_user(self._user)

    def is_share_owner(self, share: dict) -> bool:
        creator = (share.get("created_by") or share.get("creator") or "").strip()
        username = username_from_user(self._user) or ""
        if creator and username and login_matches_share_creator_field(creator, username):
            return True
        return not creator and self.is_admin_user()

    def can_edit_share_paths(self, share: dict) -> bool:
        if self.is_share_owner(share):
            return True
        username = username_from_user(self._user) or ""
        return bool(username and username in (share.get("modify_users") or []))


def _feature_disabled_json() -> JSONResponse:
    return JSONResponse({"error": FS_DISABLED_MSG}, status_code=403)


def _db_unavailable_json() -> JSONResponse:
    return JSONResponse({"error": "Database not available"}, status_code=503)


def _get_cookie(request: Request, name: str, default: str | None = None) -> str | None:
    return request.cookies.get(name, default)


def _get_secure_cookie(request: Request, name: str):
    from aird.ms.ctx import get_state
    from tornado.web import decode_signed_value

    state = get_state(request)
    secret = state.settings.get("cookie_secret")
    if not secret:
        return None
    raw = request.cookies.get(name)
    if raw is None:
        return None
    return decode_signed_value(secret, name, raw)


def _classify_share(share: dict, username: str, admin: bool) -> tuple[bool, bool]:
    creator = (share.get("created_by") or "").strip()
    allowed_raw = share.get("allowed_users")
    allowed_list = allowed_raw if isinstance(allowed_raw, list) else []
    mod_list = share.get("modify_users") or []

    if creator and username and login_matches_share_creator_field(creator, username):
        return True, False
    if not creator and admin:
        return True, False
    if username and username in mod_list:
        return False, True
    if username and isinstance(allowed_raw, list) and username in allowed_list:
        return False, True
    return False, False


def _attach_capabilities(adapter: _ShareAdapter, share: dict) -> dict:
    out = dict(share)
    out["is_owner"] = adapter.is_share_owner(share)
    out["can_revoke"] = adapter.is_share_owner(share)
    out["can_manage"] = adapter.is_share_owner(share)
    out["can_edit_paths"] = adapter.can_edit_share_paths(share)
    return out


def _redact_token(share: dict) -> dict:
    out = dict(share)
    out["has_token"] = share.get("secret_token") is not None
    out["secret_token"] = None
    return out


def _share_details(adapter: _ShareAdapter, share: dict, *, include_extended: bool = False) -> dict:
    token = share.get("secret_token")
    owner = adapter.is_share_owner(share)
    details = {
        "id": share["id"],
        "created": share.get("created", ""),
        "allowed_users": share.get("allowed_users") or [],
        "modify_users": share.get("modify_users") or [],
        "url": f"/shared/{share['id']}",
        "paths": share.get("paths", []),
        "share_type": share.get("share_type", "static"),
        "tag_name": share.get("tag_name"),
        "has_token": token is not None,
        "secret_token": token if owner else None,
    }
    if include_extended:
        details.update(
            {
                "allow_list": share.get("allow_list", []),
                "avoid_list": share.get("avoid_list", []),
                "expiry_date": share.get("expiry_date"),
                "is_owner": owner,
                "can_revoke": owner,
                "can_manage": owner,
                "can_edit_paths": adapter.can_edit_share_paths(share),
            }
        )
    return details


async def user_search(request: Request) -> Response:
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    query = request.query_params.get("q", "").strip()
    if len(query) < 2 or len(query) > 100:
        return JSONResponse({"users": []})
    conn = db_conn(request)
    service = get_service(request, "user_service")
    users = service.search_users(conn, query) if conn is not None and service else []
    return JSONResponse(
        {"users": [{"username": item["username"]} for item in users]}
    )


async def share_details_for_path(request: Request) -> Response:
    try:
        user = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    path = request.query_params.get("path", "").strip()
    if not path or len(path) > REL_PATH_MAX_LEN:
        return JSONResponse({"error": "Invalid file path"}, status_code=400)
    conn = db_conn(request)
    service = get_service(request, "share_service")
    if conn is None or service is None:
        return _db_unavailable_json()
    shares = service.get_shares_for_path(conn, path, user_root_for(user))
    adapter = _ShareAdapter(request, user)
    return JSONResponse(
        {"shares": [_share_details(adapter, share) for share in shares]}
    )


async def share_details_by_id(request: Request) -> Response:
    try:
        user = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    sid = request.query_params.get("id", "").strip()
    if not sid or len(sid) > SHARE_ID_MAX_LEN:
        return JSONResponse({"error": "Invalid share id"}, status_code=400)
    conn = db_conn(request)
    service = get_service(request, "share_service")
    share = service.get_share(conn, sid) if conn is not None and service else None
    if not share:
        return JSONResponse({"error": "Share not found"}, status_code=404)
    adapter = _ShareAdapter(request, user)
    if not adapter.can_edit_share_paths(share):
        return JSONResponse({"error": "Access denied"}, status_code=403)
    details = _share_details(adapter, share, include_extended=True)
    details["download_count"] = service.get_download_count(conn, sid)
    return JSONResponse({"share": details})


async def share_ui(request: Request) -> Response:
    try:
        require_user(request)
    except PermissionError:
        return PlainTextResponse("Authentication required", status_code=401)
    if not is_feature_enabled("file_share", True):
        return PlainTextResponse(FS_DISABLED_MSG, status_code=403)
    return render_html(request, "share.html", shares={})


async def share_list(request: Request) -> Response:
    try:
        user = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not is_feature_enabled("file_share", True):
        return _feature_disabled_json()

    conn = db_conn(request)
    if conn is None:
        return _db_unavailable_json()

    share_service = get_service(request, "share_service")
    all_shares = share_service.list_shares(conn) if share_service else {}
    adapter = _ShareAdapter(request, user)
    username = username_from_user(user) or ""
    admin = is_admin_user(user)

    my_shares: dict = {}
    shared_with_me: list = []
    for sid, share in all_shares.items():
        is_mine, is_shared = _classify_share(share, username, admin)
        if is_mine:
            my_shares[sid] = _attach_capabilities(adapter, share)
        elif is_shared:
            shared_with_me.append(_attach_capabilities(adapter, _redact_token(share)))

    return JSONResponse({"shares": my_shares, "shared_with_me": shared_with_me})


async def share_create(request: Request) -> Response:
    try:
        user = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not is_feature_enabled("file_share", True):
        return _feature_disabled_json()

    conn = db_conn(request)
    if conn is None:
        return _db_unavailable_json()

    try:
        raw = await request.body()
        if len(raw) > SHARE_JSON_BODY_MAX_BYTES:
            return JSONResponse({"error": "Request body too large"}, status_code=400)
        data = json.loads(raw.decode("utf-8") if raw else "{}")
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    try:
        check_xsrf_token(request, data.get("_xsrf"))
    except PermissionError:
        return JSONResponse({"error": "XSRF validation failed"}, status_code=403)

    vserr = validate_share_create_struct(data)
    if vserr:
        return JSONResponse({"error": vserr}, status_code=400)

    req = ShareCreateRequest.from_payload(data)
    sid = secrets.token_urlsafe(64)
    adapter = _ShareAdapter(request, user)
    root_dir = user_root_for(user)

    if req.share_type == "tag":
        if not req.tag_name:
            return JSONResponse({"error": "tag_name is required for tag shares"}, status_code=400)
        final_paths: list = []
    else:
        from aird.core.browse_paths import mounts_for_username

        final_paths, err = _resolve_share_paths(
            req.paths,
            req.share_type,
            sid,
            root_dir=root_dir,
            mounts=mounts_for_username(conn, username_from_user(user) or ""),
        )
        if err is not None:
            return JSONResponse(err[1], status_code=err[0])

    success, secret_token = _create_share_record(
        adapter,
        conn,
        sid,
        final_paths,
        req.allowed_users,
        req.disable_token,
        req.share_type,
        req.allow_list,
        req.avoid_list,
        req.expiry_date,
        modify_users=req.modify_users,
        tag_name=req.tag_name,
        created_by=username_from_user(user),
    )
    if not success:
        return JSONResponse({"error": "Failed to create share"}, status_code=500)

    audit_service = get_service(request, "audit_service")
    if audit_service:
        try:
            label = req.tag_name if req.share_type == "tag" else f"paths={len(final_paths)}"
            audit_service.log(
                conn,
                "share_create",
                username=adapter.get_display_username(),
                details=f"share_id={sid} {label}",
                ip=request.client.host if request.client else None,
            )
        except Exception:
            logger.debug("share_create audit failed", exc_info=True)

    response = ShareCreateResponse(
        share_id=sid,
        url=f"/shared/{sid}",
        secret_token=secret_token if not req.disable_token else None,
    )
    return JSONResponse(response.to_json())


async def share_revoke(request: Request) -> Response:
    try:
        user = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not is_feature_enabled("file_share", True):
        return _feature_disabled_json()

    conn = db_conn(request)
    if conn is None:
        return _db_unavailable_json()

    content_type = request.headers.get("content-type", "")
    sid = ""
    form_token = None
    if "application/json" in content_type:
        try:
            body = await request.json()
            sid = str(body.get("id", "")).strip()
            form_token = body.get("_xsrf")
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    else:
        form = await request.form()
        sid = str(form.get("id", "")).strip()
        form_token = form.get("_xsrf")

    try:
        check_xsrf_token(request, form_token)
    except PermissionError:
        return JSONResponse({"error": "XSRF validation failed"}, status_code=403)

    if not sid:
        return JSONResponse({"error": "Share id is required"}, status_code=400)
    if len(sid) > SHARE_ID_MAX_LEN:
        return JSONResponse({"error": "Invalid share id"}, status_code=400)

    share_service = get_service(request, "share_service")
    share = share_service.get_share(conn, sid) if share_service else None
    if not share:
        return JSONResponse({"error": "Share not found"}, status_code=404)

    adapter = _ShareAdapter(request, user)
    if not adapter.is_share_owner(share):
        return JSONResponse({"error": "Only the share owner can revoke this share"}, status_code=403)

    if not share_service.delete_share(conn, sid):
        return JSONResponse({"error": "Failed to delete share"}, status_code=500)

    from aird.utils.util import remove_share_cloud_dir

    remove_share_cloud_dir(sid)

    audit_service = get_service(request, "audit_service")
    if audit_service:
        try:
            audit_service.log(
                conn,
                "share_revoke",
                username=adapter.get_display_username(),
                details=f"share_id={sid}",
                ip=request.client.host if request.client else None,
            )
        except Exception:
            logger.debug("share_revoke audit failed", exc_info=True)

    if request.headers.get("Accept") == "application/json":
        return JSONResponse({"ok": True})
    return RedirectResponse("/share", status_code=302)


async def shared_list(request: Request) -> Response:
    sid = request.path_params["token"]
    conn = db_conn(request)
    if conn is None:
        return PlainTextResponse("Database not available", status_code=503)

    share_service = get_service(request, "share_service")
    share = share_service.get_share(conn, sid) if share_service else None
    if not share:
        return PlainTextResponse(INVALID_SHARE_LINK, status_code=404)
    if share_service and share_service.is_expired(share.get("expiry_date")):
        return PlainTextResponse(
            "Share expired: This share is no longer available",
            status_code=410,
        )

    allowed_ok, redirect_to_verify, user_err = _check_share_access(
        share, sid, request, _get_cookie, _get_secure_cookie
    )
    if not allowed_ok:
        if redirect_to_verify:
            return RedirectResponse(f"/shared/{sid}/verify", status_code=302)
        return PlainTextResponse(str(user_err[1]), status_code=user_err[0])

    filtered_files = _get_share_file_list(share, conn)
    can_modify, _ = _is_user_allowed_for_modify(share, _get_secure_cookie)
    files_json_safe = json.dumps(filtered_files).replace("</", "<\\/")
    return render_html(
        request,
        "shared_list.html",
        share_id=sid,
        files=filtered_files,
        files_json=files_json_safe,
        can_modify=can_modify,
    )


def _share_or_error(request: Request, sid: str):
    conn = db_conn(request)
    if conn is None:
        return None, PlainTextResponse("Database not available", status_code=503)
    service = get_service(request, "share_service")
    share = service.get_share(conn, sid) if service else None
    if not share:
        return None, PlainTextResponse(INVALID_SHARE_LINK, status_code=404)
    if service and service.is_expired(share.get("expiry_date")):
        return None, PlainTextResponse("Share expired", status_code=410)
    return share, None


def _token_attempt_allowed(request: Request) -> bool:
    ip = request.client.host if request.client else ""
    now = time.monotonic()
    with _TOKEN_ATTEMPTS_LOCK:
        attempts, started = _TOKEN_ATTEMPTS.get(ip, (0, now))
        if now - started > 300:
            attempts, started = 0, now
        if attempts >= 10:
            return False
        _TOKEN_ATTEMPTS[ip] = (attempts + 1, started)
        return True


async def shared_verify_get(request: Request) -> Response:
    sid = request.path_params["token"]
    share, error = _share_or_error(request, sid)
    if error:
        return error
    if not share.get("secret_token"):
        return RedirectResponse(f"/shared/{sid}", status_code=302)
    allowed, _, _ = _check_share_access(
        share, sid, request, _get_cookie, _get_secure_cookie
    )
    if allowed:
        return RedirectResponse(f"/shared/{sid}", status_code=302)
    return render_html(request, "token_verification.html", share_id=sid)


async def shared_verify_post(request: Request) -> Response:
    if not _token_attempt_allowed(request):
        return JSONResponse({"error": "Too many attempts"}, status_code=429)
    sid = request.path_params["token"]
    share, error = _share_or_error(request, sid)
    if error:
        return error
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    provided = str(data.get("token") or "").strip()
    if len(provided) > ACCESS_TOKEN_MAX_LEN:
        return JSONResponse({"error": "Token is too long"}, status_code=400)
    stored = str(share.get("secret_token") or "")
    if not provided or not stored or not secrets.compare_digest(provided, stored):
        return JSONResponse({"error": "Invalid token"}, status_code=403)
    response = JSONResponse({"success": True})
    response.set_cookie(
        f"share_token_{sid}",
        provided,
        path=f"/shared/{sid}",
        max_age=3600,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return response


async def shared_file(request: Request) -> Response:
    sid = request.path_params["token"]
    path = str(request.path_params["path"])
    share, error = _share_or_error(request, sid)
    if error:
        return error
    allowed, _, access_error = _check_share_access(
        share, sid, request, _get_cookie, _get_secure_cookie
    )
    if not allowed:
        status, message = access_error or (403, "Access denied")
        return PlainTextResponse(str(message), status_code=status)
    rel = path.replace("\\", "/").lstrip("/")
    normalized = posixpath.normpath(rel)
    if normalized.startswith("..") or os.path.isabs(normalized):
        return PlainTextResponse("Access denied", status_code=403)
    conn = db_conn(request)
    if not _is_path_in_share(share, normalized, conn):
        return PlainTextResponse("File is not part of this share", status_code=403)
    abspath, confine = resolve_share_rel(share, normalized, conn)
    if not abspath or not confine or not is_within_root(abspath, confine):
        return PlainTextResponse("Access denied", status_code=403)
    if not os.path.isfile(abspath):
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(
        abspath,
        filename=os.path.basename(abspath),
        media_type="application/octet-stream",
    )


async def favorites_list(request: Request) -> Response:
    try:
        user = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not is_feature_enabled("favorites", True):
        return JSONResponse({"error": "Favorites disabled"}, status_code=403)

    conn = db_conn(request)
    if conn is None:
        return _db_unavailable_json()

    username = username_from_user(user)
    if not username:
        return JSONResponse({"error": "Could not resolve username"}, status_code=401)

    favorites_service = get_service(request, "favorites_service")
    favorites = favorites_service.get_favorites(conn, username) if favorites_service else []
    return JSONResponse({"favorites": favorites})


async def favorites_toggle(request: Request) -> Response:
    try:
        user = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not is_feature_enabled("favorites", True):
        return JSONResponse({"error": "Favorites disabled"}, status_code=403)

    conn = db_conn(request)
    if conn is None:
        return _db_unavailable_json()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    try:
        check_xsrf_token(request, body.get("_xsrf"))
    except PermissionError:
        return JSONResponse({"error": "XSRF validation failed"}, status_code=403)

    path = str(body.get("path", "")).strip()
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    if len(path) > REL_PATH_MAX_LEN:
        return JSONResponse({"error": "path is too long"}, status_code=400)

    username = username_from_user(user)
    if not username:
        return JSONResponse({"error": "Could not resolve username"}, status_code=401)

    favorites_service = get_service(request, "favorites_service")
    if favorites_service is None:
        return JSONResponse({"error": "Favorites service unavailable"}, status_code=503)

    try:
        is_fav = favorites_service.toggle(conn, username, path)
    except Exception:
        logger.exception("favorites toggle failed")
        return JSONResponse({"error": "Failed to toggle favorite"}, status_code=500)

    return JSONResponse({"favorited": is_fav, "path": path})


routes = [
    Route("/share", share_ui, methods=["GET"]),
    Route("/share/list", share_list, methods=["GET"]),
    Route("/share/create", share_create, methods=["POST"]),
    Route("/share/revoke", share_revoke, methods=["POST"]),
    Route("/api/users/search", user_search, methods=["GET"]),
    Route("/api/share/details", share_details_for_path, methods=["GET"]),
    Route("/api/share/details_by_id", share_details_by_id, methods=["GET"]),
    Route("/shared/{token}/verify", shared_verify_get, methods=["GET"]),
    Route("/shared/{token}/verify", shared_verify_post, methods=["POST"]),
    Route("/shared/{token}/file/{path:path}", shared_file, methods=["GET"]),
    Route("/shared/{token}", shared_list, methods=["GET"]),
    Route("/api/favorites", favorites_list, methods=["GET"]),
    Route("/api/favorites/toggle", favorites_toggle, methods=["POST"]),
]

__all__ = ["routes"]
