"""Authentication routes (login, logout, profile, sessions)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from aird.constants.input_limits import (
    InputTooLongError,
    SAFE_NEXT_URL_MAX_LEN,
)
from aird.db.sessions import (
    SESSION_COOKIE_NAME,
    list_sessions_for_user,
    revoke_session_for_user,
)
from aird.db.shares import list_shares_accessible_to_user
from aird.domain.contracts import AuthRequest
from aird.handlers.auth_handlers import (
    _TOKEN_ONLY_PROFILE_USERNAMES,
    _apply_session_cookies,
    _session_public_view,
    check_login_rate_limit,
    terminate_server_session,
)

_XSRF_RELOAD_MSG = "Your session expired. Reload the page and try again."
from aird.handlers.constants import (
    ADMIN_LOGIN_TEMPLATE,
    ADMIN_URL,
    DB_NOT_AVAILABLE_MSG,
    FILES_BASE_URL,
    INVALID_CREDENTIALS_MSG,
    LOGIN_HTML,
    PROFILE_TEMPLATE,
    TOO_MANY_LOGIN_ATTEMPTS_MSG,
)
from aird.ms.ctx import (
    _AuthAdapter,
    check_xsrf_token,
    clear_cookie,
    current_user,
    get_state,
    set_signed_cookie,
)
from aird.ms.render import render_html

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


class _RequestView:
    """Tornado-compatible request view for auth_handlers helpers."""

    def __init__(self, request: Request):
        self._request = request
        self.remote_ip = _client_ip(request)
        self.headers = request.headers
        self.method = request.method


class _HandlerBridge:
    """Minimal BaseHandler stand-in for ported auth_handlers helpers."""

    def __init__(
        self,
        request: Request,
        settings: dict[str, Any],
        form: dict[str, str] | None = None,
    ):
        self._raw_request = request
        self.request = _RequestView(request)
        self.settings = settings
        self._form = form or {}
        self._render_spec: tuple[str, dict[str, Any]] | None = None
        self._redirect_url: str | None = None
        self._status = 200
        self._json: Any = None
        self._headers: dict[str, str] = {}
        self._cookie_sets: list[tuple[str, str, dict[str, Any]]] = []
        self._cookie_clears: list[str] = []

    @property
    def db_conn(self):
        ctx = self.settings.get("app_context")
        if ctx is not None and getattr(ctx, "db_conn", None) is not None:
            return ctx.db_conn
        return self.settings.get("db_conn")

    def get_service(self, name: str, default=None):
        ctx = self.settings.get("app_context")
        if ctx is not None:
            return ctx.get_service(name, default)
        return default

    def get_secure_cookie(self, name: str, value: Any = None, max_age_days: float = 31):
        return _AuthAdapter(self._raw_request, self.settings).get_secure_cookie(
            name, value, max_age_days=max_age_days
        )

    def get_cookie(self, name: str, default: str | None = None) -> str | None:
        return self._raw_request.cookies.get(name, default)

    def get_argument(self, name: str, default: str = "") -> str:
        val = self._form.get(name, default)
        return val if val is not None else default

    @property
    def current_user(self):
        return current_user(self._raw_request)

    def is_admin_user(self) -> bool:
        user = self.current_user
        if isinstance(user, dict) and user.get("role") == "admin":
            return True
        return bool(self.get_secure_cookie("admin"))

    def get_session_id(self) -> str | None:
        user = self.current_user
        if isinstance(user, dict) and user.get("_session_id"):
            return str(user["_session_id"])
        cookie = self.get_secure_cookie(SESSION_COOKIE_NAME)
        if not cookie:
            return None
        return cookie.decode("utf-8") if isinstance(cookie, bytes) else str(cookie)

    def set_secure_cookie(self, name: str, value: str | bytes, **opts: Any) -> None:
        self._cookie_sets.append((name, str(value), opts))

    def clear_cookie(self, name: str) -> None:
        self._cookie_clears.append(name)

    def clear_auth_cookies(self) -> None:
        for name in (SESSION_COOKIE_NAME, "user", "user_role", "admin"):
            self.clear_cookie(name)

    def regenerate_session(self) -> None:
        self.clear_auth_cookies()
        self.clear_cookie("_xsrf")

    def session_cookie_opts(self, expires_days: int = 1) -> dict[str, Any]:
        return {
            "httponly": True,
            "secure": self._raw_request.url.scheme == "https",
            "samesite": "Strict",
            "expires_days": expires_days,
        }

    def publish_event(self, event: Any) -> None:
        ctx = self.settings.get("app_context")
        if ctx is not None and getattr(ctx, "event_bus", None) is not None:
            ctx.event_bus.publish(event)

    def render(self, template: str, **kwargs: Any) -> None:
        self._render_spec = (template, kwargs)

    def redirect(self, url: str) -> None:
        self._redirect_url = url

    def set_status(self, code: int) -> None:
        self._status = code

    def write(self, data: Any) -> None:
        self._json = data

    def set_header(self, name: str, value: str) -> None:
        self._headers[name] = value

    def apply_cookies(self, response: Response) -> None:
        for name in self._cookie_clears:
            clear_cookie(response, name)
        for name, value, opts in self._cookie_sets:
            days = opts.get("expires_days", 1)
            max_age = int(float(days) * 86400)
            set_signed_cookie(
                response,
                self.settings,
                name,
                value,
                max_age=max_age,
            )


def _bridge_response(request: Request, bridge: _HandlerBridge) -> Response:
    if bridge._redirect_url:
        response: Response = RedirectResponse(bridge._redirect_url, status_code=302)
    elif bridge._render_spec:
        template, kwargs = bridge._render_spec
        response = render_html(request, template, **kwargs)
    elif bridge._json is not None:
        response = JSONResponse(bridge._json, status_code=bridge._status)
        for key, value in bridge._headers.items():
            response.headers[key] = value
    else:
        response = Response(status_code=bridge._status)
    bridge.apply_cookies(response)
    return response


def _settings(request: Request) -> dict[str, Any]:
    return get_state(request).settings


def _is_safe_redirect_url(url: str) -> bool:
    if not url:
        return False
    if not url.startswith("/") or url.startswith("//"):
        return False
    if ":" in url.split("/")[0]:
        return False
    return True


def _safe_next_url(request: Request, default: str = FILES_BASE_URL) -> str:
    next_url = request.query_params.get("next", default)
    if len(next_url) > SAFE_NEXT_URL_MAX_LEN:
        return default
    if _is_safe_redirect_url(next_url):
        return next_url
    return default


def _require_user(request: Request) -> dict[str, Any] | RedirectResponse:
    user = current_user(request)
    if not user:
        next_path = quote(str(request.url.path))
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return RedirectResponse(f"/login?next={quote(next_path)}", status_code=302)
    return user


async def _form_dict(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def login_get(request: Request):
    settings = _settings(request)
    user = current_user(request)
    next_url = _safe_next_url(request)

    if user:
        if isinstance(user, dict) and user.get("must_change_password"):
            mq = quote(next_url)
            return RedirectResponse(f"/auth/mandatory-password?next={mq}", status_code=302)
        return RedirectResponse(next_url, status_code=302)

    sso_response = _authentik_sso_response(request, settings, next_url)
    if sso_response is not None:
        return sso_response

    return render_html(
        request,
        LOGIN_HTML,
        error=None,
        settings=settings,
        next_url=next_url,
    )


def _record_authentik_last_login(bridge: _HandlerBridge, user: dict) -> None:
    user_id = user.get("id")
    if user_id is None or bridge.db_conn is None:
        return
    stamp = datetime.now().isoformat()
    try:
        service = bridge.get_service("user_service")
        if service is not None:
            service.update_user(bridge.db_conn, user_id, last_login=stamp)
        else:
            from aird.db.users import update_user

            update_user(bridge.db_conn, user_id, last_login=stamp)
    except Exception:
        logger.warning("Authentik auto-login could not update last_login")


def _finish_authentik_login(
    request: Request, settings: dict[str, Any], user: dict, next_url: str
) -> Response:
    bridge = _HandlerBridge(request, settings)
    _record_authentik_last_login(bridge, user)
    _apply_session_cookies(bridge, str(user["username"]), str(user.get("role") or "user"))
    if user.get("must_change_password"):
        bridge.redirect(f"/auth/mandatory-password?next={quote(next_url)}")
    else:
        bridge.redirect(next_url)
    return _bridge_response(request, bridge)


def _authentik_sso_response(
    request: Request, settings: dict[str, Any], next_url: str
) -> Response | None:
    from aird.core.authentik_sso import (
        ACCESS_DENIED_HTML,
        decide_authentik_login,
        load_authentik_sso,
    )
    from aird.ms.deps import db_conn as request_db
    from aird.ms.deps import get_service

    conn = request_db(request)
    config = load_authentik_sso(conn)

    def lookup(username: str):
        if conn is None:
            return None
        service = get_service(request, "user_service")
        if service is not None:
            return service.get_user(conn, username)
        from aird.db.users import get_user_by_username

        return get_user_by_username(conn, username)

    decision = decide_authentik_login(config, request.headers, lookup)
    if decision.action == "forbidden":
        return HTMLResponse(ACCESS_DENIED_HTML, status_code=403)
    if decision.action == "session" and decision.user and decision.user.get("username"):
        return _finish_authentik_login(request, settings, decision.user, next_url)
    return None


async def login_post(request: Request):
    settings = _settings(request)
    form = await _form_dict(request)
    next_url = _safe_next_url(request)

    if not check_login_rate_limit(_client_ip(request)):
        resp = render_html(
            request,
            LOGIN_HTML,
            error=TOO_MANY_LOGIN_ATTEMPTS_MSG,
            settings=settings,
            next_url=next_url,
        )
        resp.status_code = 429
        return resp

    try:
        check_xsrf_token(request, form.get("_xsrf"))
    except PermissionError:
        return render_html(
            request,
            LOGIN_HTML,
            error=_XSRF_RELOAD_MSG,
            settings=settings,
            next_url=next_url,
        )

    bridge = _HandlerBridge(request, settings, form)
    try:
        auth_req = AuthRequest.from_handler(bridge)
        username = auth_req.username
        password = auth_req.password
        token = auth_req.token
    except InputTooLongError:
        return render_html(
            request,
            LOGIN_HTML,
            error="Input is too long. Please shorten username, password, or token.",
            settings=settings,
            next_url=next_url,
        )
    except Exception:
        logger.exception("Error parsing login form data")
        return render_html(
            request,
            LOGIN_HTML,
            error="Error processing login request. Please try again.",
            settings=settings,
            next_url=FILES_BASE_URL,
        )

    from aird.handlers.auth_handlers import (
        _try_token_login,
        _try_username_password_login,
    )

    if token and _try_token_login(bridge, token, next_url):
        return _bridge_response(request, bridge)
    if username and password and _try_username_password_login(
        bridge, username, password, next_url
    ):
        return _bridge_response(request, bridge)
    if username or password:
        return render_html(
            request,
            LOGIN_HTML,
            error=INVALID_CREDENTIALS_MSG,
            settings=settings,
            next_url=next_url,
        )
    return render_html(
        request,
        LOGIN_HTML,
        error="Username/password or token is required.",
        settings=settings,
        next_url=next_url,
    )


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


async def logout_get(request: Request):
    logger.warning(
        "Deprecated GET /logout from %s; use POST with XSRF",
        _client_ip(request),
    )
    return RedirectResponse("/login", status_code=302)


async def logout_post(request: Request):
    settings = _settings(request)
    form = await _form_dict(request)
    try:
        check_xsrf_token(request, form.get("_xsrf"))
    except PermissionError:
        return RedirectResponse("/login", status_code=302)

    bridge = _HandlerBridge(request, settings, form)
    terminate_server_session(bridge)
    bridge.redirect("/login")
    return _bridge_response(request, bridge)


# ---------------------------------------------------------------------------
# Admin login
# ---------------------------------------------------------------------------


async def admin_login_get(request: Request):
    settings = _settings(request)
    bridge = _HandlerBridge(request, settings)
    user = bridge.current_user

    if user and isinstance(user, dict) and user.get("must_change_password"):
        n = quote(ADMIN_URL)
        return RedirectResponse(f"/auth/mandatory-password?next={n}", status_code=302)
    if bridge.is_admin_user():
        return RedirectResponse(ADMIN_URL, status_code=302)

    return render_html(request, ADMIN_LOGIN_TEMPLATE, error=None)


async def admin_login_post(request: Request):
    settings = _settings(request)
    form = await _form_dict(request)

    if not check_login_rate_limit(_client_ip(request)):
        resp = render_html(
            request,
            ADMIN_LOGIN_TEMPLATE,
            error=TOO_MANY_LOGIN_ATTEMPTS_MSG,
        )
        resp.status_code = 429
        return resp

    try:
        check_xsrf_token(request, form.get("_xsrf"))
    except PermissionError:
        return render_html(request, ADMIN_LOGIN_TEMPLATE, error=_XSRF_RELOAD_MSG)

    bridge = _HandlerBridge(request, settings, form)
    try:
        auth_req = AuthRequest.from_handler(bridge)
    except InputTooLongError:
        return render_html(
            request,
            ADMIN_LOGIN_TEMPLATE,
            error="Input is too long. Please shorten username, password, or token.",
        )

    from aird.handlers.auth_handlers import (
        _try_admin_token_login,
        _try_admin_username_password_login,
    )

    username = auth_req.username
    password = auth_req.password
    token = auth_req.token

    if token and _try_admin_token_login(bridge, token):
        return _bridge_response(request, bridge)
    if username and password and _try_admin_username_password_login(
        bridge, username, password
    ):
        return _bridge_response(request, bridge)
    if username or password or token:
        return render_html(request, ADMIN_LOGIN_TEMPLATE, error=INVALID_CREDENTIALS_MSG)
    return render_html(
        request,
        ADMIN_LOGIN_TEMPLATE,
        error="Username/password or token is required.",
    )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


async def profile_get(request: Request):
    auth = _require_user(request)
    if isinstance(auth, RedirectResponse):
        return auth

    settings = _settings(request)
    ldap_enabled = settings.get("ldap_server") is not None
    quota = {"quota_bytes": None, "used_bytes": 0}
    user = auth
    shared_with_me: list = []
    passkeys: list = []

    if isinstance(user, dict) and user.get("username", "") in _TOKEN_ONLY_PROFILE_USERNAMES:
        user = None

    bridge = _HandlerBridge(request, settings)
    db_conn = bridge.db_conn
    if user and db_conn:
        username = user.get("username", "") if isinstance(user, dict) else str(user)
        quota = bridge.get_service("user_service").get_user_quota(db_conn, username)
        shared_with_me = list_shares_accessible_to_user(db_conn, username)

    if user and db_conn:
        from aird.db import webauthn as webauthn_db
        from aird.utils.util import is_feature_enabled

        uname = user.get("username", "") if isinstance(user, dict) else str(user)
        if is_feature_enabled("webauthn", False) and uname not in (
            "token_user",
            "admin_token",
        ):
            passkeys = webauthn_db.list_credentials(db_conn, uname)

    user_plugins = []
    if user:
        from aird.plugins.access import user_facing_plugins

        uname = user.get("username", "") if isinstance(user, dict) else str(user)
        user_plugins = user_facing_plugins(uname, db_conn)

    return render_html(
        request,
        PROFILE_TEMPLATE,
        user=user,
        error=None,
        success=None,
        ldap_enabled=ldap_enabled,
        quota=quota,
        shared_with_me=shared_with_me,
        passkeys=passkeys,
        user_plugins=user_plugins,
    )


# ---------------------------------------------------------------------------
# Sessions API
# ---------------------------------------------------------------------------


async def sessions_list(request: Request):
    auth = _require_user(request)
    if isinstance(auth, RedirectResponse):
        return auth

    username = auth.get("username", "") if isinstance(auth, dict) else str(auth)
    if username in _TOKEN_ONLY_PROFILE_USERNAMES:
        return JSONResponse(
            {"error": "Sessions are not available for token logins."},
            status_code=403,
        )

    settings = _settings(request)
    bridge = _HandlerBridge(request, settings)
    if bridge.db_conn is None:
        return JSONResponse({"error": DB_NOT_AVAILABLE_MSG}, status_code=500)

    sessions = list_sessions_for_user(bridge.db_conn, username)
    current_id = bridge.get_session_id()
    return JSONResponse(
        {
            "sessions": [
                _session_public_view(session, current_id) for session in sessions
            ]
        }
    )


async def session_revoke(request: Request):
    auth = _require_user(request)
    if isinstance(auth, RedirectResponse):
        return auth

    session_id = request.path_params["session_id"]
    username = auth.get("username", "") if isinstance(auth, dict) else str(auth)
    if username in _TOKEN_ONLY_PROFILE_USERNAMES:
        return JSONResponse(
            {"error": "Sessions are not available for token logins."},
            status_code=403,
        )

    settings = _settings(request)
    bridge = _HandlerBridge(request, settings)
    if bridge.db_conn is None:
        return JSONResponse({"error": DB_NOT_AVAILABLE_MSG}, status_code=500)

    current_id = bridge.get_session_id()
    revoked = revoke_session_for_user(bridge.db_conn, session_id, username)
    if not revoked:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    logged_out = session_id == current_id
    if logged_out:
        terminate_server_session(bridge)
        response = JSONResponse({"revoked": True, "logged_out": logged_out})
        bridge.apply_cookies(response)
        return response

    return JSONResponse({"revoked": True, "logged_out": logged_out})


routes = [
    Route("/login", login_get, methods=["GET"]),
    Route("/login", login_post, methods=["POST"]),
    Route("/logout", logout_get, methods=["GET"]),
    Route("/logout", logout_post, methods=["POST"]),
    Route("/admin/login", admin_login_get, methods=["GET"]),
    Route("/admin/login", admin_login_post, methods=["POST"]),
    Route("/profile", profile_get, methods=["GET"]),
    Route("/api/sessions", sessions_list, methods=["GET"]),
    Route("/api/sessions/{session_id}/revoke", session_revoke, methods=["POST"]),
]
