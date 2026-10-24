"""Admin UI routes (Starlette)."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import socket

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

import aird.constants as constants_module
from aird.constants import UPLOAD_CONFIG
from aird.db.policies import list_policies
from aird.db.resource_tags import list_resource_tags
from aird.db.tag_colors import get_tag_colors_map
from aird.db.user_attributes import list_all_user_attributes
from aird.handlers.base_handler import DISPLAY_ADMIN_TOKEN
from aird.core.security import validate_password
from aird.ms.ctx import check_xsrf_token
from aird.ms.deps import db_conn, get_service, is_admin_user, require_user
from aird.ms.render import render_html
from aird.network_share_manager import (
    is_smb_server_available,
    is_webdav_server_available,
    smb_library_available,
    webdav_library_available,
)
from aird.utils.tag_display import tag_chip_inline_style
from aird.plugins.chat import chat_library_available
from aird.plugins.gitlab import gitlab_library_available

logger = logging.getLogger(__name__)


def require_admin(request: Request):
    """Return the current user if admin, else raise PermissionError."""
    user = require_user(request)
    if not is_admin_user(user):
        raise PermissionError("Admin access required")
    return user


def _forbidden() -> PlainTextResponse:
    return PlainTextResponse("Access denied", status_code=403)


async def admin_dashboard(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    from aird.utils.util import (
        get_current_feature_flags,
        get_current_websocket_config,
        invalidate_feature_flags_cache,
        invalidate_websocket_config_cache,
    )

    invalidate_feature_flags_cache()
    invalidate_websocket_config_cache()
    current_features = get_current_feature_flags()
    conn = db_conn(request)
    config_service = get_service(request, "config_service")

    ldap_enabled = bool(get_state_settings(request).get("ldap_server"))
    allowed_current = set(constants_module.UPLOAD_ALLOWED_EXTENSIONS)
    runtime_config = constants_module.get_effective_transfer_strategy()
    if conn is not None and config_service is not None:
        try:
            allowed_current = config_service.load_allowed_extensions(conn)
            runtime_config = config_service.get_runtime_config(conn)
        except Exception:
            logger.debug("admin dashboard config load failed", exc_info=True)

    from aird.core.authentik_sso import load_authentik_sso, public_authentik_sso

    try:
        authentik_sso = public_authentik_sso(load_authentik_sso(conn))
    except Exception:
        logger.debug("authentik sso config load failed", exc_info=True)
        authentik_sso = public_authentik_sso({})

    return render_html(
        request,
        "admin.html",
        features=current_features,
        websocket_config=get_current_websocket_config(),
        upload_config=UPLOAD_CONFIG,
        runtime_config=runtime_config,
        ldap_enabled=ldap_enabled,
        authentik_sso=authentik_sso,
        available_extensions=sorted(
            set(constants_module.ALLOWED_UPLOAD_EXTENSIONS) | set(allowed_current)
        ),
        allowed_extensions_current=allowed_current,
        smb_library_available=smb_library_available(),
        webdav_library_available=webdav_library_available(),
        chat_library_available=chat_library_available(),
        gitlab_library_available=gitlab_library_available(),
    )


def get_state_settings(request: Request) -> dict:
    from aird.ms.ctx import get_state

    return get_state(request).settings


async def admin_users(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    users: list = []
    conn = db_conn(request)
    user_service = get_service(request, "user_service")
    if conn is not None and user_service is not None:
        try:
            users = user_service.list_users(conn)
        except Exception:
            logger.debug("admin users list failed", exc_info=True)

    return render_html(
        request,
        "admin_users.html",
        users=users,
        reset_password_plain=None,
        reset_for_username=None,
    )


def _valid_username(username: str) -> bool:
    return 3 <= len(username) <= 50 and bool(
        re.fullmatch(r"[A-Za-z0-9_-]+", username)
    )


async def admin_user_create(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()
    if request.method == "GET":
        return render_html(request, "admin_user_create.html", error=None)
    form = await request.form()
    try:
        check_xsrf_token(request, str(form.get("_xsrf") or ""))
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    role = str(form.get("role") or "user")
    if not _valid_username(username):
        return render_html(request, "admin_user_create.html", error="Invalid username")
    valid, error = validate_password(password)
    if not valid:
        return render_html(request, "admin_user_create.html", error=error)
    if role not in {"user", "admin"}:
        return render_html(request, "admin_user_create.html", error="Invalid role")
    conn = db_conn(request)
    service = get_service(request, "user_service")
    if conn is None or service is None:
        return PlainTextResponse("Database not available", status_code=503)
    try:
        service.create_user(conn, username, password, role=role)
    except (ValueError, RuntimeError) as exc:
        return render_html(request, "admin_user_create.html", error=str(exc))
    return RedirectResponse("/admin/users", status_code=303)


async def admin_user_edit(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()
    try:
        user_id = int(request.path_params["user_id"])
    except (TypeError, ValueError):
        return PlainTextResponse("Invalid user id", status_code=400)
    conn = db_conn(request)
    service = get_service(request, "user_service")
    users = service.list_users(conn) if conn is not None and service else []
    user = next((item for item in users if int(item["id"]) == user_id), None)
    if user is None:
        return PlainTextResponse("User not found", status_code=404)
    if request.method == "GET":
        return render_html(
            request,
            "admin_user_edit.html",
            user=user,
            error=None,
            settings=get_state_settings(request),
        )
    form = await request.form()
    try:
        check_xsrf_token(request, str(form.get("_xsrf") or ""))
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    role = str(form.get("role") or "user")
    active = str(form.get("active") or "") == "on"
    error = None
    if not _valid_username(username):
        error = "Invalid username"
    elif role not in {"user", "admin"}:
        error = "Invalid role"
    elif password:
        valid, error = validate_password(password)
        if valid:
            error = None
    if error:
        return render_html(
            request,
            "admin_user_edit.html",
            user=user,
            error=error,
            settings=get_state_settings(request),
        )
    updates = {"username": username, "role": role, "active": active}
    if password:
        updates["password"] = password
    if not service.update_user(conn, user_id, **updates):
        return PlainTextResponse("Failed to update user", status_code=500)
    return RedirectResponse("/admin/users", status_code=303)


async def admin_audit(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    audit_service = get_service(request, "audit_service")
    limit = min(1000, max(1, int(request.query_params.get("limit", "500"))))
    offset = max(0, int(request.query_params.get("offset", "0")))

    if request.query_params.get("format") == "csv":
        rows = audit_service.get_logs(conn, limit=10000, offset=0) if audit_service and conn else []
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "created_at", "username", "action", "details", "ip"])
        for row in rows:
            writer.writerow(
                [
                    row.get("id"),
                    row.get("created_at"),
                    row.get("username") or "",
                    row.get("action") or "",
                    row.get("details") or "",
                    row.get("ip") or "",
                ]
            )
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
        )

    logs = (
        audit_service.get_logs(conn, limit=limit, offset=offset)
        if audit_service and conn
        else []
    )
    return render_html(
        request,
        "admin_audit.html",
        logs=logs,
        limit=limit,
        offset=offset,
        logs_count=len(logs),
    )


async def admin_tags(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    tags = list_resource_tags(conn) if conn is not None else []
    tag_colors = get_tag_colors_map(conn) if conn is not None else {}
    return render_html(
        request,
        "admin_tags.html",
        tags=tags,
        tag_colors=tag_colors,
        tag_chip_style=tag_chip_inline_style,
        error=request.query_params.get("error", ""),
    )


async def admin_policies(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    policies = list_policies(conn) if conn is not None else []
    for policy in policies:
        policy["condition_json_str"] = json.dumps(policy.get("condition") or {}, indent=2)
    return render_html(
        request,
        "admin_policies.html",
        policies=policies,
        error=request.query_params.get("error", ""),
    )


async def admin_network_shares(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    network_share_service = get_service(request, "network_share_service")
    shares = network_share_service.list_all(conn) if conn and network_share_service else []
    for idx, share in enumerate(shares):
        share["running"] = False
        share["mount_idx"] = idx

    server_host = socket.getfqdn()
    if server_host in ("localhost", "localhost.localdomain", ""):
        try:
            server_host = socket.gethostbyname(socket.gethostname())
        except OSError:
            server_host = "127.0.0.1"

    return render_html(
        request,
        "admin_network_shares.html",
        shares=shares,
        error=request.query_params.get("error"),
        server_host=server_host,
        server_host_js=json.dumps(server_host),
        smb_server_available=is_smb_server_available(),
        webdav_server_available=is_webdav_server_available(),
    )


def _path_usernames(request: Request, conn) -> list[str]:
    service = get_service(request, "user_service")
    users = service.list_users(conn) if conn is not None and service else []
    names = []
    for u in users or []:
        name = str(u.get("username") or "").strip()
        if name:
            names.append(name)
    return sorted(set(names), key=str.lower)


def _assign_path_error(message: str) -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(f"/admin/paths?error={quote(message)}", status_code=303)


async def admin_user_paths(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    from aird.core.browse_paths import sanitize_mount_name
    from aird.db.user_mounts import ALL_USERS, insert_path_mount, list_all_path_mounts

    conn = db_conn(request)
    if request.method == "POST":
        form = await request.form()
        try:
            check_xsrf_token(request, str(form.get("_xsrf") or ""))
        except PermissionError:
            return PlainTextResponse("XSRF validation failed", status_code=403)
        if conn is None:
            return _assign_path_error("Database unavailable")
        host_raw = str(form.get("host_path") or "").strip()
        mount_name = sanitize_mount_name(str(form.get("mount_name") or ""))
        writable = str(form.get("writable") or "") in {"on", "true", "1"}
        everyone = str(form.get("everyone") or "") in {"on", "true", "1"}
        usernames = [
            str(v).strip() for v in form.getlist("usernames") if str(v).strip()
        ]
        if not host_raw or not os.path.isabs(host_raw):
            return _assign_path_error("Host path must be absolute")
        host_path = os.path.realpath(host_raw)
        if not os.path.isdir(host_path):
            return _assign_path_error("Folder does not exist")
        if not mount_name:
            return _assign_path_error("Invalid folder name")
        targets = [ALL_USERS] if everyone else usernames
        if not targets:
            return _assign_path_error("Select at least one user")
        created_by = str(require_admin(request).get("username") or "admin")
        for username in targets:
            mid = insert_path_mount(
                conn,
                username=username,
                host_path=host_path,
                mount_name=mount_name,
                writable=writable,
                created_by=created_by,
            )
            if mid is None:
                return _assign_path_error("Name already assigned for that user")
        audit = get_service(request, "audit_service")
        if audit:
            audit.log(
                conn,
                "user_path_mount_create",
                username=created_by,
                details=f"name={mount_name} host={host_path} users={','.join(targets)}",
                ip=request.client.host if request.client else None,
            )
        return RedirectResponse("/admin/paths", status_code=303)

    mounts = list_all_path_mounts(conn) if conn is not None else []
    return render_html(
        request,
        "admin_user_paths.html",
        mounts=mounts,
        users=_path_usernames(request, conn),
        error=request.query_params.get("error"),
    )


async def admin_user_path_delete(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()
    form = await request.form()
    try:
        check_xsrf_token(request, str(form.get("_xsrf") or ""))
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)
    from aird.db.user_mounts import delete_path_mount

    conn = db_conn(request)
    try:
        mount_id = int(form.get("mount_id") or 0)
    except (TypeError, ValueError):
        mount_id = 0
    if conn is not None and mount_id and delete_path_mount(conn, mount_id):
        audit = get_service(request, "audit_service")
        if audit:
            audit.log(
                conn,
                "user_path_mount_delete",
                username=str(require_admin(request).get("username") or "admin"),
                details=f"id={mount_id}",
                ip=request.client.host if request.client else None,
            )
    return RedirectResponse("/admin/paths", status_code=303)


async def admin_user_attributes(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    attrs = list_all_user_attributes(conn) if conn is not None else []
    return render_html(
        request,
        "admin_user_attributes.html",
        attrs=attrs,
        error=request.query_params.get("error", ""),
    )


def _plugin_usernames(raw: str) -> list[str]:
    from aird.constants.input_limits import PLUGIN_MAX_USERNAMES, PLUGIN_USERNAME_MAX_LEN

    names: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").replace(",", " ").split():
        name = part.strip()
        if not name or len(name) > PLUGIN_USERNAME_MAX_LEN or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= PLUGIN_MAX_USERNAMES:
            break
    return names


async def admin_plugins(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    from aird.plugins.access import (
        SCOPE_ALL,
        SCOPE_NONE,
        SCOPE_USERS,
        catalog,
        get_assignment,
        set_assignment,
    )
    from aird.utils.util import get_current_feature_flags

    conn = db_conn(request)
    if request.method == "POST":
        form = await request.form()
        try:
            check_xsrf_token(request, str(form.get("_xsrf") or ""))
        except PermissionError:
            return PlainTextResponse("XSRF validation failed", status_code=403)
        if conn is None:
            return PlainTextResponse("Database not available", status_code=503)
        allowed = {SCOPE_NONE, SCOPE_ALL, SCOPE_USERS}
        for item in catalog():
            pid = item["id"]
            scope = str(form.get(f"scope_{pid}") or SCOPE_NONE).strip()
            if scope not in allowed:
                scope = SCOPE_NONE
            set_assignment(conn, pid, scope, _plugin_usernames(str(form.get(f"usernames_{pid}") or "")))
        from aird.plugins.onedrive.settings import save_settings
        from aird.plugins.onedrive_browser.settings import save_settings as save_browser_settings

        save_settings(
            conn,
            root_path=str(form.get("onedrive_root_path") or ""),
            sync_interval_minutes=str(form.get("onedrive_sync_interval") or 10),
            auth_source=str(form.get("onedrive_auth_source") or ""),
            client_id=str(form.get("onedrive_client_id") or ""),
            tenant=str(form.get("onedrive_tenant") or "common"),
            token_env_var=str(form.get("onedrive_token_env_var") or ""),
        )
        from aird.plugins.onedrive.settings import clear_token_blob, save_stored_access_token

        if str(form.get("onedrive_token_clear") or "") == "1":
            clear_token_blob(conn)
        od_token = str(form.get("onedrive_token") or "").strip()
        if od_token:
            save_stored_access_token(conn, od_token)
        save_browser_settings(
            conn,
            client_id=str(form.get("onedrive_browser_client_id") or ""),
            tenant=str(form.get("onedrive_browser_tenant") or "common"),
        )
        from aird.plugins.gitlab.settings import normalize_boards, save_settings as save_gitlab_settings
        import json as _json

        boards_raw = str(form.get("gitlab_boards_json") or "[]")
        try:
            boards = normalize_boards(_json.loads(boards_raw))
        except (TypeError, ValueError, _json.JSONDecodeError):
            boards = []
        save_gitlab_settings(
            conn,
            default_host=str(form.get("gitlab_default_host") or ""),
            boards=boards,
            token_source=str(form.get("gitlab_token_source") or ""),
            token_env_var=str(form.get("gitlab_token_env_var") or ""),
        )
        from aird.plugins.gitlab.settings import clear_stored_token, save_stored_token

        if str(form.get("gitlab_token_clear") or "") == "1":
            clear_stored_token(conn)
        token = str(form.get("gitlab_token") or "").strip()
        if token:
            save_stored_token(conn, token)
        selected = str(form.get("selected_plugin") or "").strip()
        suffix = f"&p={selected}" if selected else ""
        return RedirectResponse(f"/admin/plugins?saved=1{suffix}", status_code=303)

    flags = get_current_feature_flags()
    plugins = []
    for item in catalog():
        assignment = get_assignment(conn, item["id"])
        plugins.append(
            {
                **item,
                **assignment,
                "flag_on": bool(flags.get(item["flag"])),
                "usernames_text": "\n".join(assignment.get("usernames") or []),
            }
        )
    users = []
    service = get_service(request, "user_service")
    if conn is not None and service is not None:
        try:
            users = service.list_users(conn) or []
        except Exception:
            users = []
    usernames = [u.get("username") for u in users if u.get("username")]
    from aird.plugins.onedrive.settings import get_settings
    from aird.plugins.onedrive_browser.settings import get_settings as get_browser_settings
    from aird.plugins.gitlab.settings import get_settings as get_gitlab_settings

    selected = (request.query_params.get("p") or "").strip()
    if not selected and plugins:
        selected = plugins[0]["id"]

    return render_html(
        request,
        "admin_plugins.html",
        plugins=plugins,
        usernames=usernames,
        saved=request.query_params.get("saved") == "1",
        selected_plugin=selected,
        onedrive_settings=get_settings(conn),
        onedrive_browser_settings=get_browser_settings(conn),
        gitlab_settings=get_gitlab_settings(conn),
    )


async def admin_authentik_sso(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()
    try:
        check_xsrf_token(request)
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    conn = db_conn(request)
    if conn is None:
        return JSONResponse({"ok": False, "error": "Database not available"}, status_code=503)
    from aird.core.authentik_sso import public_authentik_sso, save_authentik_sso

    try:
        saved = save_authentik_sso(conn, payload)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, **public_authentik_sso(saved)})


async def admin_feature_flag(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()
    try:
        check_xsrf_token(request)
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    key = str(payload.get("flag") or payload.get("key") or "").strip()
    raw = payload.get("enabled", payload.get("value"))
    if isinstance(raw, str):
        enabled = raw.strip().lower() in {"1", "true", "on", "yes"}
    else:
        enabled = bool(raw)
    config_service = get_service(request, "config_service")
    if config_service is None:
        return JSONResponse({"ok": False, "error": "Config service unavailable"}, status_code=503)
    result = config_service.apply_feature_flag(db_conn(request), key, enabled)
    status = 200 if result.get("ok") else int(result.get("status") or 400)
    return JSONResponse(result, status_code=status)


async def admin_onedrive_device_start(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()
    try:
        check_xsrf_token(request)
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)
    conn = db_conn(request)
    from aird.plugins.onedrive.auth import OneDriveAuthError, start_device_flow
    from aird.plugins.onedrive.settings import get_settings, save_device_pending

    settings = get_settings(conn)
    try:
        flow = start_device_flow(settings["client_id"], settings["tenant"])
    except OneDriveAuthError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    save_device_pending(conn, flow)
    return JSONResponse(
        {
            "ok": True,
            "user_code": flow.get("user_code"),
            "verification_uri": flow.get("verification_uri"),
            "interval": flow.get("interval"),
            "expires_in": flow.get("expires_in"),
        }
    )


async def admin_onedrive_device_poll(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()
    try:
        check_xsrf_token(request)
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)
    conn = db_conn(request)
    from aird.plugins.onedrive.auth import OneDriveAuthError, poll_device_flow
    from aird.plugins.onedrive.settings import (
        clear_device_pending,
        get_settings,
        load_device_pending,
        save_device_tokens,
    )

    settings = get_settings(conn)
    pending = load_device_pending(conn)
    device_code = pending.get("device_code")
    if not device_code:
        return JSONResponse({"ok": False, "error": "No device login in progress"}, status_code=400)
    try:
        tokens = poll_device_flow(settings["client_id"], settings["tenant"], device_code)
    except OneDriveAuthError as exc:
        if exc.pending:
            return JSONResponse({"ok": False, "pending": True})
        clear_device_pending(conn)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    save_device_tokens(conn, tokens)
    return JSONResponse({"ok": True, "token_configured": True})


routes = [
    Route("/admin", admin_dashboard, methods=["GET"]),
    Route("/admin/users", admin_users, methods=["GET"]),
    Route("/admin/users/create", admin_user_create, methods=["GET", "POST"]),
    Route("/admin/users/edit/{user_id:int}", admin_user_edit, methods=["GET", "POST"]),
    Route("/admin/audit", admin_audit, methods=["GET"]),
    Route("/admin/tags", admin_tags, methods=["GET"]),
    Route("/admin/policies", admin_policies, methods=["GET"]),
    Route("/admin/network-shares", admin_network_shares, methods=["GET"]),
    Route("/admin/paths", admin_user_paths, methods=["GET", "POST"]),
    Route("/admin/paths/delete", admin_user_path_delete, methods=["POST"]),
    Route("/admin/user-attributes", admin_user_attributes, methods=["GET"]),
    Route("/admin/plugins", admin_plugins, methods=["GET", "POST"]),
    Route("/admin/api/onedrive/device-start", admin_onedrive_device_start, methods=["POST"]),
    Route("/admin/api/onedrive/device-poll", admin_onedrive_device_poll, methods=["POST"]),
    Route("/admin/api/feature-flags", admin_feature_flag, methods=["POST"]),
    Route("/admin/api/authentik-sso", admin_authentik_sso, methods=["POST"]),
]

__all__ = ["routes", "require_admin", "DISPLAY_ADMIN_TOKEN"]
