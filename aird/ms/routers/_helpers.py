"""Shared helpers for Starlette routers."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from aird.ms.ctx import check_xsrf_token, current_user, get_state
from aird.utils.util import is_feature_enabled


def require_user(request: Request) -> dict[str, Any] | RedirectResponse:
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return user


def has_modify_privileges(user: dict[str, Any]) -> bool:
    role = str(user.get("role", "user")).lower()
    return role in {"admin", "user"}


def app_context(request: Request) -> Any:
    return get_state(request).settings.get("app_context")


def db_conn(request: Request) -> Any:
    state = get_state(request)
    ctx = state.settings.get("app_context")
    if ctx is not None:
        return ctx.db_conn
    return state.settings.get("db_conn")


def get_service(request: Request, name: str, default: Any = None) -> Any:
    state = get_state(request)
    services = state.settings.get("services")
    if services is None and state.settings.get("app_context") is not None:
        services = state.settings["app_context"].services
    if services:
        return services.get(name, default)
    ctx = app_context(request)
    if ctx is not None:
        return ctx.get_service(name, default)
    return default


def username_for_db(request: Request, user: dict[str, Any]) -> str | None:
    name = user.get("username")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def resolve_request_rel(request: Request, user: dict[str, Any], rel: str):
    from aird.core.browse_paths import resolve_for_user
    from aird.ms.ctx import user_root_for

    return resolve_for_user(
        username_for_db(request, user) or "",
        rel,
        user_root_for(user),
        db_conn(request),
    )


def request_mounts(request: Request, user: dict[str, Any]) -> list:
    from aird.core.browse_paths import mounts_for_username

    return mounts_for_username(db_conn(request), username_for_db(request, user) or "")


def is_admin_user(user: dict[str, Any]) -> bool:
    role = str(user.get("role", "")).lower()
    username = str(user.get("username", ""))
    return role == "admin" or username in ("admin_token", "admin")


def text_response(status: int, body: str) -> PlainTextResponse:
    return PlainTextResponse(body, status_code=status)


def json_response(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def feature_disabled_response(body: str) -> PlainTextResponse:
    return PlainTextResponse(body, status_code=403)


def require_feature(key: str, default: bool, body: str) -> Response | None:
    if not is_feature_enabled(key, default):
        return feature_disabled_response(body)
    return None


async def check_post_xsrf(request: Request) -> tuple[str | None, Response | None]:
    form = await request.form()
    token = form.get("_xsrf")
    form_token = str(token) if token is not None else None
    try:
        check_xsrf_token(request, form_token)
    except PermissionError:
        return None, PlainTextResponse("XSRF validation failed", status_code=403)
    return form_token, None


def wants_json(request: Request) -> bool:
    accept = request.headers.get("accept") or ""
    return accept == "application/json"
