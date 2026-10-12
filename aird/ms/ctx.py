"""ASGI request context, cookies, and Tornado-compatible signed cookies."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request

import aird.constants as constants_module
from aird.handlers.base_handler import (
    DISPLAY_ACCESS_TOKEN,
    DISPLAY_ADMIN_TOKEN,
    _TOKEN_ONLY_USERNAMES,
    _display_username_from_dict,
    authenticate_handler,
    json_encode_for_script,
)
from aird.utils.util import is_feature_enabled

# Reuse Tornado's signed-cookie codec so existing sessions keep working
# until we rotate to a pure itsdangerous scheme.
from tornado.web import create_signed_value, decode_signed_value


@dataclass
class AppState:
    settings: dict[str, Any] = field(default_factory=dict)
    template_path: str = ""
    static_path: str = ""
    db_conn: Any = None
    services: dict[str, Any] = field(default_factory=dict)


def get_state(request: Request) -> AppState:
    return request.app.state.aird


class _AuthAdapter:
    """Minimal object so authenticate_handler() works without Tornado RequestHandler."""

    def __init__(self, request: Request, settings: dict[str, Any]):
        self.request = request
        self.application = type("A", (), {"settings": settings})()
        self.settings = settings
        self.cookies = request.cookies

    def get_secure_cookie(self, name: str, value: Any = None, max_age_days: float = 31):
        secret = self.settings.get("cookie_secret")
        if not secret:
            return None
        raw = self.request.cookies.get(name)
        if raw is None:
            return None
        return decode_signed_value(secret, name, raw, max_age_days=max_age_days)

    def get_cookie(self, name: str, default: str | None = None) -> str | None:
        return self.request.cookies.get(name, default)


def current_user(request: Request) -> Any:
    state = get_state(request)
    # Prefer Caddy-forwarded identity when present
    hdr_user = request.headers.get("x-aird-user")
    if hdr_user:
        role = request.headers.get("x-aird-role") or "user"
        return {"username": hdr_user, "role": role, "via": "caddy"}
    return authenticate_handler(_AuthAdapter(request, state.settings))


def set_signed_cookie(
    response,
    settings: dict[str, Any],
    name: str,
    value: str | bytes,
    *,
    httponly: bool = True,
    max_age: int | None = None,
) -> None:
    secret = settings.get("cookie_secret")
    if not secret:
        raise RuntimeError("cookie_secret not configured")
    signed = create_signed_value(secret, name, value)
    if isinstance(signed, bytes):
        signed = signed.decode("utf-8")
    response.set_cookie(
        name,
        signed,
        httponly=httponly,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def clear_cookie(response, name: str) -> None:
    response.delete_cookie(name, path="/")


def user_root_for(user: Any) -> str:
    from aird.core.user_storage import user_data_dir_for_username

    if not constants_module.MULTI_USER:
        return constants_module.ROOT_DIR
    if not user:
        return constants_module.ROOT_DIR
    username = user.get("username", "") if isinstance(user, dict) else str(user)
    if not username or username in _TOKEN_ONLY_USERNAMES:
        return constants_module.ROOT_DIR
    return user_data_dir_for_username(
        username,
        root_dir=constants_module.ROOT_DIR,
        multi_user=True,
    )


def csp_nonce() -> str:
    return secrets.token_urlsafe(16)


def security_headers(nonce: str, https: bool = False) -> dict[str, str]:
    from aird.core.csp import connect_src

    connect = connect_src()
    csp = (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'unsafe-inline'; "
        f"font-src 'self' data:; "
        f"img-src 'self' data: blob: https:; "
        f"connect-src {connect}; "
        f"worker-src 'self'; "
        f"frame-src 'self' blob:; "
        f"object-src 'self' blob:; "
        f"frame-ancestors 'none'; "
    )
    headers = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Embedder-Policy": "credentialless",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


class _TemplateHandler:
    """Tornado ``handler`` surface still referenced by shared templates."""

    def __init__(self, request: Request, user: Any):
        self.request = request
        self.current_user = user

    def get_current_user(self) -> Any:
        return self.current_user

    def get_display_username(self) -> str:
        if not self.current_user:
            return "Guest"
        if isinstance(self.current_user, dict):
            return _display_username_from_dict(self.current_user)
        return str(self.current_user)

    def static_url(self, path: str) -> str:
        return f"/static/{path.lstrip('/')}"


def template_globals(request: Request, nonce: str, **extra: Any) -> dict[str, Any]:
    from aird.plugins.access import bind_template_plugin_checks

    user = current_user(request)
    state = get_state(request)
    show_admin = False
    if isinstance(user, dict):
        show_admin = user.get("role") == "admin" or user.get("username") in (
            "admin_token",
            DISPLAY_ADMIN_TOKEN,
        )
    username = user.get("username") if isinstance(user, dict) else None
    ns = {
        "csp_nonce": nonce,
        "is_feature_enabled": is_feature_enabled,
        "json_encode_for_script": json_encode_for_script,
        "nav_search_path": "/search",
        "nav_title": "",
        "is_admin_user": show_admin,
        "ldap_enabled": bool(state.settings.get("ldap_server")),
        "current_user": user,
        "handler": _TemplateHandler(request, user),
        "request": request,
        "static_url": lambda p: f"/static/{p.lstrip('/')}",
        "xsrf_form_html": lambda: _xsrf_form_html(request),
        **bind_template_plugin_checks(username, state.db_conn),
        **extra,
    }
    return ns


def _xsrf_form_html(request: Request) -> str:
    token = request.cookies.get("_xsrf") or ""
    # Prefer unsigned cookie value for form field; ensure set by middleware
    return f'<input type="hidden" name="_xsrf" value="{token}"/>'


def ensure_xsrf_cookie(request: Request, response) -> str:
    token = request.cookies.get("_xsrf")
    if not token:
        token = secrets.token_hex(16)
        response.set_cookie("_xsrf", token, httponly=False, samesite="lax", path="/")
    return token


def check_xsrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie = request.cookies.get("_xsrf") or ""
    header = request.headers.get("x-xsrftoken") or ""
    form = ""
    # form token checked by callers that parse form data
    provided = header or form
    if not cookie or not provided or not secrets.compare_digest(cookie, provided):
        # allow form body to supply later — raise if header missing and no form yet
        if not header:
            raise PermissionError("XSRF validation failed")


def check_xsrf_token(request: Request, form_token: str | None = None) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie = request.cookies.get("_xsrf") or ""
    provided = (
        request.headers.get("x-xsrftoken")
        or form_token
        or ""
    )
    if not cookie or not provided or not secrets.compare_digest(cookie, provided):
        raise PermissionError("XSRF validation failed")
