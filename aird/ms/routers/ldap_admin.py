"""LDAP admin routes (Starlette) — mounted when LDAP_ENABLED."""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from aird.constants.admin import (
    ALL_FIELDS_REQUIRED,
    CONFIG_NAME_LENGTH,
    CONFIG_NOT_FOUND,
    INVALID_CONFIG_ID,
    SYNC_STARTED,
    TEMPLATE_LDAP_CONFIG_CREATE,
    TEMPLATE_LDAP_CONFIG_EDIT,
    URL_ADMIN_LDAP,
)
from aird.database.ldap import (
    create_ldap_config,
    delete_ldap_config,
    get_all_ldap_configs,
    get_ldap_config_by_id,
    get_ldap_sync_logs,
    update_ldap_config,
)
from aird.ms.ctx import check_xsrf_token
from aird.ms.deps import db_conn
from aird.ms.render import render_html
from aird.ms.routers.admin import require_admin

logger = logging.getLogger(__name__)


def _forbidden() -> PlainTextResponse:
    return PlainTextResponse("Access denied", status_code=403)


async def ldap_list(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    configs = get_all_ldap_configs(conn) if conn is not None else []
    sync_logs = get_ldap_sync_logs(conn, limit=20) if conn is not None else []
    return render_html(
        request,
        "admin_ldap.html",
        configs=configs,
        sync_logs=sync_logs,
    )


async def ldap_create_get(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()
    return render_html(request, TEMPLATE_LDAP_CONFIG_CREATE, error=None)


async def ldap_create_post(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    if conn is None:
        return PlainTextResponse("Database not available", status_code=503)

    form = await request.form()
    try:
        check_xsrf_token(request, str(form.get("_xsrf") or ""))
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)

    name = str(form.get("name", "")).strip()
    server = str(form.get("server", "")).strip()
    ldap_base_dn = str(form.get("ldap_base_dn", "")).strip()
    ldap_member_attributes = str(form.get("ldap_member_attributes", "member")).strip()
    user_template = str(form.get("user_template", "")).strip()

    if not all([name, server, ldap_base_dn, user_template]):
        return render_html(
            request, TEMPLATE_LDAP_CONFIG_CREATE, error=ALL_FIELDS_REQUIRED
        )
    if len(name) < 3 or len(name) > 50:
        return render_html(
            request, TEMPLATE_LDAP_CONFIG_CREATE, error=CONFIG_NAME_LENGTH
        )

    try:
        create_ldap_config(
            conn,
            name,
            server,
            ldap_base_dn,
            ldap_member_attributes,
            user_template,
        )
        return RedirectResponse(URL_ADMIN_LDAP, status_code=302)
    except ValueError as exc:
        return render_html(request, TEMPLATE_LDAP_CONFIG_CREATE, error=str(exc))
    except Exception:
        logger.exception("LDAP config creation error")
        return render_html(
            request,
            TEMPLATE_LDAP_CONFIG_CREATE,
            error="Error creating configuration. Please try again.",
        )


async def ldap_edit_get(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    if conn is None:
        return PlainTextResponse("Database not available", status_code=503)

    try:
        config_id = int(request.path_params["config_id"])
    except (TypeError, ValueError):
        return PlainTextResponse(INVALID_CONFIG_ID, status_code=400)

    config = get_ldap_config_by_id(conn, config_id)
    if not config:
        return PlainTextResponse(CONFIG_NOT_FOUND, status_code=404)
    return render_html(request, TEMPLATE_LDAP_CONFIG_EDIT, config=config, error=None)


async def ldap_edit_post(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    if conn is None:
        return PlainTextResponse("Database not available", status_code=503)

    try:
        config_id = int(request.path_params["config_id"])
    except (TypeError, ValueError):
        return PlainTextResponse(INVALID_CONFIG_ID, status_code=400)

    config = get_ldap_config_by_id(conn, config_id)
    if not config:
        return PlainTextResponse(CONFIG_NOT_FOUND, status_code=404)

    form = await request.form()
    try:
        check_xsrf_token(request, str(form.get("_xsrf") or ""))
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)

    name = str(form.get("name", "")).strip()
    server = str(form.get("server", "")).strip()
    ldap_base_dn = str(form.get("ldap_base_dn", "")).strip()
    ldap_member_attributes = str(form.get("ldap_member_attributes", "member")).strip()
    user_template = str(form.get("user_template", "")).strip()
    active = str(form.get("active", "off")) == "on"

    if not all([name, server, ldap_base_dn, user_template]):
        return render_html(
            request,
            TEMPLATE_LDAP_CONFIG_EDIT,
            config=config,
            error=ALL_FIELDS_REQUIRED,
        )
    if len(name) < 3 or len(name) > 50:
        return render_html(
            request,
            TEMPLATE_LDAP_CONFIG_EDIT,
            config=config,
            error=CONFIG_NAME_LENGTH,
        )

    try:
        if update_ldap_config(
            conn,
            config_id,
            name=name,
            server=server,
            ldap_base_dn=ldap_base_dn,
            ldap_member_attributes=ldap_member_attributes,
            user_template=user_template,
            active=active,
        ):
            return RedirectResponse(URL_ADMIN_LDAP, status_code=302)
        return render_html(
            request,
            TEMPLATE_LDAP_CONFIG_EDIT,
            config=config,
            error="Failed to update configuration",
        )
    except Exception:
        logger.exception("LDAP config update error")
        from aird.constants.admin import ERROR_UPDATE_CONFIG

        return render_html(
            request,
            TEMPLATE_LDAP_CONFIG_EDIT,
            config=config,
            error=ERROR_UPDATE_CONFIG,
        )


async def ldap_delete(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return _forbidden()

    conn = db_conn(request)
    if conn is None:
        return PlainTextResponse("Database not available", status_code=503)

    form = await request.form()
    try:
        check_xsrf_token(request, str(form.get("_xsrf") or ""))
    except PermissionError:
        return PlainTextResponse("XSRF validation failed", status_code=403)

    try:
        config_id = int(str(form.get("config_id", "0")))
    except ValueError:
        return PlainTextResponse(INVALID_CONFIG_ID, status_code=400)

    if config_id <= 0:
        return PlainTextResponse(INVALID_CONFIG_ID, status_code=400)

    if delete_ldap_config(conn, config_id):
        return RedirectResponse(URL_ADMIN_LDAP, status_code=302)
    return PlainTextResponse(CONFIG_NOT_FOUND, status_code=404)


async def ldap_sync(request: Request) -> Response:
    try:
        require_admin(request)
    except PermissionError:
        return JSONResponse({"error": "Access denied"}, status_code=403)
    return JSONResponse({"status": SYNC_STARTED})


routes = [
    Route("/admin/ldap", ldap_list, methods=["GET"]),
    Route("/admin/ldap/create", ldap_create_get, methods=["GET"]),
    Route("/admin/ldap/create", ldap_create_post, methods=["POST"]),
    Route("/admin/ldap/edit/{config_id:int}", ldap_edit_get, methods=["GET"]),
    Route("/admin/ldap/edit/{config_id:int}", ldap_edit_post, methods=["POST"]),
    Route("/admin/ldap/delete", ldap_delete, methods=["POST"]),
    Route("/admin/ldap/sync", ldap_sync, methods=["POST"]),
]

__all__ = ["routes"]
