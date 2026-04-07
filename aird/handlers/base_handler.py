import base64
import functools
import json
import logging
import os
import secrets
from typing import Any, Callable, Protocol

import tornado.web
import tornado.websocket

import aird.config as config_module
import aird.constants as constants_module
from aird.core.security import sanitize_username_for_folder
from aird.db import get_user_by_username
from aird.handlers.constants import DB_NOT_AVAILABLE_MSG
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)
DISPLAY_ADMIN_TOKEN = "Admin (Token)"  # nosec B105
DISPLAY_ACCESS_TOKEN = "Access (Token)"  # nosec B105

# Usernames that represent anonymous/token-based access (no personal folder)
_TOKEN_ONLY_USERNAMES = {"token_user", "admin_token"}


def get_user_root(handler) -> str:
    """Return the effective root directory for the current user.

    In single-user mode (default), this simply returns ``constants.ROOT_DIR``.
    In multi-user mode, each authenticated user gets a private subdirectory
    under ``ROOT_DIR`` named after their sanitised username.

    Falls back to ``ROOT_DIR`` when:
    - Multi-user mode is disabled
    - The user is not authenticated
    - The user is a token-only user (no personal folder)
    - The username cannot be sanitised to a safe folder name
    """
    if not constants_module.MULTI_USER:
        return constants_module.ROOT_DIR

    user = handler.get_current_user() if hasattr(handler, "get_current_user") else None
    if not user:
        return constants_module.ROOT_DIR

    username = user.get("username", "") if isinstance(user, dict) else str(user)
    if not username or username in _TOKEN_ONLY_USERNAMES:
        return constants_module.ROOT_DIR

    safe_name = sanitize_username_for_folder(username)
    if not safe_name:
        logger.warning(
            "Cannot create safe folder for username %r, using global root", username
        )
        return constants_module.ROOT_DIR

    user_root = os.path.join(constants_module.ROOT_DIR, safe_name)
    os.makedirs(user_root, exist_ok=True)
    return user_root


# ---------------------------------------------------------------------------
# Decorators for common guard patterns
# ---------------------------------------------------------------------------


def require_db(method):
    """Decorator: ensures database connection is available via settings.

    If the connection is ``None`` the handler returns **500** with a JSON
    error body and the wrapped method is never called.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self.db_conn is None:
            self.set_status(500)
            self.write({"error": DB_NOT_AVAILABLE_MSG})
            return
        return method(self, *args, **kwargs)

    return wrapper


def require_admin(
    deny_status=403,
    deny_body="Access denied",
    redirect_url=None,
):
    """Decorator factory: ensures the current user is an admin.

    Parameters
    ----------
    deny_status : int
        HTTP status code when the user is not an admin (ignored when
        *redirect_url* is set).
    deny_body : str | dict
        Response body when the user is not an admin (ignored when
        *redirect_url* is set).
    redirect_url : str | None
        If set, redirect non-admin users instead of returning an error
        response.
    """

    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            if not self.is_admin_user():
                if redirect_url:
                    self.redirect(redirect_url)
                else:
                    self.set_status(deny_status)
                    self.write(deny_body)
                return
            return method(self, *args, **kwargs)

        return wrapper

    return decorator


def require_modify_access(
    deny_status=403,
    deny_body="Write access denied. Sign in with modify privileges.",
):
    """Decorator: ensure caller has modify/write privileges."""

    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            if hasattr(self, "has_modify_privileges") and self.has_modify_privileges():
                return method(self, *args, **kwargs)
            self.set_status(deny_status)
            self.write(deny_body)
            return None

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Mixin for WebSocket handlers using WebSocketConnectionManager
# ---------------------------------------------------------------------------


class ManagedWebSocketMixin:
    """Mixin that centralises add/remove connection-manager boiler-plate.

    Subclasses must define a class-level ``connection_manager`` attribute
    (a :class:`~aird.utils.util.WebSocketConnectionManager` instance).

    Call ``self.register_connection()`` from ``open()``; it sends the
    "connection limit exceeded" message and closes when the limit is
    reached, returning ``False`` so the caller can bail out.

    ``on_close`` is automatically handled — it calls
    ``remove_connection`` for you.  Override ``on_close`` if you need
    extra cleanup, but remember to call ``super().on_close()``.
    """

    def register_connection(self) -> bool:
        """Try to register this connection. Returns True on success."""
        if not self.connection_manager.add_connection(self):
            self.write_message(
                json.dumps(
                    {
                        "type": "error",
                        "message": "Connection limit exceeded. Please try again later.",
                    }
                )
            )
            self.close(code=1013, reason="Connection limit exceeded")
            return False
        return True

    def on_close(self):
        self.connection_manager.remove_connection(self)

    def get_current_user(self):
        # WebSocket handler doesn't have a default auth mechanism, so we explicitly call authenticate_handler
        return authenticate_handler(self)


class XSRFTokenMixin:
    """Mixin for handlers that need X-XSRFToken header support for JSON requests."""

    def check_xsrf_cookie(self):
        """Override CSRF check to support X-XSRFToken header for JSON requests"""
        # Get token from cookie (expected value)
        cookie_token = self.get_cookie("_xsrf")
        if not cookie_token:
            raise tornado.web.HTTPError(403, "'_xsrf' argument missing from POST")

        # Get token from header or POST data
        provided_token = self.request.headers.get("X-XSRFToken")
        if not provided_token:
            # Fallback to POST argument for form submissions
            provided_token = self.get_argument("_xsrf", None)
        if not provided_token:
            raise tornado.web.HTTPError(403, "'_xsrf' argument missing from POST")

        # Compare tokens using constant-time comparison
        if not secrets.compare_digest(provided_token, cookie_token):
            raise tornado.web.HTTPError(403, "XSRF cookie does not match POST argument")


def _parse_username_from_cookie(user_json):
    """Parse username from cookie value (JSON or raw string/bytes). Returns (username, True if parsed from JSON)."""
    try:
        user_data = json.loads(user_json)
        return (user_data.get("username", ""), True)
    except (json.JSONDecodeError, TypeError):
        raw = (
            user_json.decode("utf-8")
            if isinstance(user_json, bytes)
            else str(user_json)
        )
        return (raw, False)


class AuthStrategy(Protocol):
    """Authentication strategy contract."""

    def authenticate(self, handler: Any) -> dict[str, Any] | None: ...


class CookieAuthStrategy:
    """Authenticate a user from signed cookie state."""

    @staticmethod
    def _token_user_from_username(handler: Any, username: str) -> dict[str, Any] | None:
        if username == "token_authenticated":
            return {"username": "token_user", "role": "user"}
        if username == "admin_token_authenticated":
            if handler.get_secure_cookie("admin"):
                return {"username": "admin_token", "role": "admin"}
            return None
        return None

    @classmethod
    def _token_user_from_cookie_bytes(
        cls, handler: Any, user_json: Any
    ) -> dict[str, Any] | None:
        if not isinstance(user_json, bytes):
            return None
        user_str = user_json.decode("utf-8", errors="ignore")
        return cls._token_user_from_username(handler, user_str)

    def authenticate(self, handler: Any) -> dict[str, Any] | None:
        user_json = handler.get_secure_cookie("user")
        if not user_json:
            return None
        try:
            username, _ = _parse_username_from_cookie(user_json)
            db_conn = handler.settings.get("db_conn")
            if db_conn:
                user = get_user_by_username(db_conn, username)
                if user:
                    return user
            return self._token_user_from_username(handler, username)
        except Exception:
            return self._token_user_from_cookie_bytes(handler, user_json)


class BearerAuthStrategy:
    """Authenticate a user from Bearer token."""

    def authenticate(self, handler: Any) -> dict[str, Any] | None:
        auth_header = handler.request.headers.get("Authorization")
        if not isinstance(auth_header, str) or not auth_header.startswith("Bearer "):
            return None
        token = auth_header.split(" ")[1]
        current_access_token = config_module.ACCESS_TOKEN
        if not isinstance(current_access_token, str) or not current_access_token:
            return None
        normalized_token = token.strip()
        normalized_access_token = current_access_token.strip()
        if secrets.compare_digest(normalized_token, normalized_access_token):
            return {"username": "token_user", "role": "user"}
        return None


class ChainedAuthStrategy:
    """Run auth strategies in order until one succeeds."""

    def __init__(self, strategies: list[AuthStrategy]):
        self._strategies = strategies

    def authenticate(self, handler: Any) -> dict[str, Any] | None:
        for strategy in self._strategies:
            user = strategy.authenticate(handler)
            if user is not None:
                return user
        return None


_COOKIE_AUTH_STRATEGY = CookieAuthStrategy()
_BEARER_AUTH_STRATEGY = BearerAuthStrategy()
_DEFAULT_AUTH_STRATEGY = ChainedAuthStrategy(
    [_COOKIE_AUTH_STRATEGY, _BEARER_AUTH_STRATEGY]
)


def _try_cookie_auth(handler):
    """Attempt auth via secure cookie. Returns user dict or None."""
    return _COOKIE_AUTH_STRATEGY.authenticate(handler)


def _try_bearer_auth(handler):
    """Attempt auth via Authorization Bearer token. Returns user dict or None."""
    return _BEARER_AUTH_STRATEGY.authenticate(handler)


def authenticate_handler(handler):
    """Shared authentication logic for both HTTP and WebSocket handlers.

    Checks secure cookie and Bearer token auth. Returns a user dict or None.
    The *handler* must support get_secure_cookie() and request.headers.
    """
    return _DEFAULT_AUTH_STRATEGY.authenticate(handler)


def get_username_string_for_db(handler) -> str | None:
    """Return the login username string for SQLite (e.g. favorites), not the whole user dict.

    ``get_current_user()`` / ``current_user`` may be a dict from ``authenticate_handler``;
    DB helpers expect a plain ``str`` username.
    """
    if not hasattr(handler, "get_current_user"):
        return None
    user = handler.get_current_user()
    if user is None:
        return None
    if isinstance(user, dict):
        name = user.get("username")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None
    if isinstance(user, bytes):
        return user.decode("utf-8", errors="replace")
    return str(user)


def _display_username_from_dict(user):
    """Return display string for dict user (from get_current_user)."""
    username = user.get("username", "")
    role = user.get("role", "")
    if username == "admin_token" and role == "admin":
        return DISPLAY_ADMIN_TOKEN
    if username == "token_user" and role == "user":
        return DISPLAY_ACCESS_TOKEN
    if role == "admin":
        return f"{username} (Admin)"
    if role:
        return f"{username} (User)"
    return username or "Guest"


def _display_username_from_legacy(user, handler):
    """Return display string for bytes/str user (legacy support)."""
    user_str = (
        user.decode("utf-8", errors="ignore") if isinstance(user, bytes) else str(user)
    )
    if user_str == "token_authenticated":
        role_cookie = handler.get_secure_cookie("user_role")
        if isinstance(role_cookie, bytes):
            role = role_cookie.decode("utf-8", errors="ignore")
        elif role_cookie:
            role = str(role_cookie)
        else:
            role = ""
        return DISPLAY_ACCESS_TOKEN if role != "admin" else DISPLAY_ADMIN_TOKEN
    if user_str == "admin_token_authenticated":
        return DISPLAY_ADMIN_TOKEN
    role_cookie = handler.get_secure_cookie("user_role")
    if not role_cookie:
        return user_str
    role = (
        role_cookie.decode("utf-8", errors="ignore")
        if isinstance(role_cookie, bytes)
        else str(role_cookie)
    )
    if role == "admin":
        return f"{user_str} (Admin)"
    if role:
        return f"{user_str} (User)"
    return user_str


class BaseHandler(tornado.web.RequestHandler):
    _TOKEN_ONLY_USERNAMES = {"token_user", "admin_token"}

    @property
    def app_context(self):
        return self.settings.get("app_context")

    @property
    def db_conn(self):
        if self.app_context is not None:
            return self.app_context.db_conn
        return self.settings.get("db_conn")

    @property
    def feature_flags(self):
        if self.app_context is not None:
            return self.app_context.feature_flags
        return self.settings.get("feature_flags", {})

    @property
    def cloud_manager(self):
        if self.app_context is not None:
            return self.app_context.cloud_manager
        return self.settings.get("cloud_manager")

    @property
    def network_share_manager(self):
        if self.app_context is not None:
            return self.app_context.network_share_manager
        return self.settings.get("network_share_manager")

    @property
    def room_manager(self):
        if self.app_context is not None:
            return self.app_context.room_manager
        return self.settings.get("room_manager")

    @property
    def event_bus(self):
        if self.app_context is not None:
            return self.app_context.event_bus
        return self.settings.get("event_bus")

    @property
    def event_metrics(self):
        if self.app_context is not None:
            return self.app_context.event_metrics
        return self.settings.get("event_metrics")

    def get_service(self, name: str, default=None):
        services = self.settings.get("services", {})
        if self.app_context is not None:
            return self.app_context.get_service(name, default)
        return services.get(name, default)

    def publish_event(self, event: Any) -> None:
        bus = self.event_bus
        if bus is not None:
            bus.publish(event)

    def get_signed_in_user(self) -> dict[str, Any] | None:
        """Return non-token authenticated user object when available."""
        user = self.get_current_user()
        if not isinstance(user, dict):
            return None
        username = user.get("username")
        if not isinstance(username, str) or not username.strip():
            return None
        if username in self._TOKEN_ONLY_USERNAMES:
            return None
        return user

    def has_modify_privileges(self) -> bool:
        user = self.get_signed_in_user()
        if not user:
            return False
        role = str(user.get("role", "user")).lower()
        return role in {"admin", "user"}

    def require_modify_privileges(
        self,
        *,
        status: int = 403,
        body: str = "Write access denied. Sign in with modify privileges.",
    ) -> bool:
        if self.has_modify_privileges():
            return True
        self.set_status(status)
        self.write(body)
        return False

    def write_json_error(self, status: int, message: str) -> None:
        self.set_status(status)
        self.write({"error": message})

    def require_db_connection(self, message: str = DB_NOT_AVAILABLE_MSG):
        db_conn = self.db_conn
        if db_conn is None:
            self.write_json_error(500, message)
            return None
        return db_conn

    def run_json_action(
        self,
        action: Callable[[], dict[str, Any] | None],
        *,
        on_error_message: str,
        on_error_status: int = 500,
    ) -> dict[str, Any] | None:
        """Template-style execution flow for JSON endpoints."""
        try:
            payload = action()
            if payload is not None:
                self.write(payload)
            return payload
        except tornado.web.HTTPError as exc:
            reason = exc.log_message or exc.reason or "Request failed"
            self.write_json_error(exc.status_code, reason)
            return None
        except Exception as exc:
            logger.error("JSON action failed: %s", exc, exc_info=True)
            self.write_json_error(on_error_status, on_error_message)
            return None

    def require_feature(
        self, feature_key: str, default=True, *, status=403, body=None
    ) -> bool:
        """Check feature flag. If disabled, set status, write body, and return False. Else return True."""
        if is_feature_enabled(feature_key, default):
            return True
        self.set_status(status)
        self.write(body if body is not None else "Feature disabled.")
        return False

    def session_cookie_opts(self, expires_days=1) -> dict:
        """Return common kwargs for secure session cookies (httponly, secure, samesite, expires_days)."""
        return {
            "httponly": True,
            "secure": self.request.protocol == "https",
            "samesite": "Strict",
            "expires_days": expires_days,
        }

    def parse_json_body(self, default=None):
        """Parse request body as JSON. Returns default if body is empty or invalid."""
        if default is None:
            default = {}
        raw = self.request.body or b"{}"
        try:
            return json.loads(
                raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            )
        except Exception:
            return default

    def prepare(self):
        """Generate a unique nonce for this request for CSP."""
        # Generate a cryptographically secure random nonce (16 bytes = 128 bits)
        self._csp_nonce = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")

    def get_csp_nonce(self):
        """Return the CSP nonce for this request."""
        if not hasattr(self, "_csp_nonce"):
            self._csp_nonce = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
        return self._csp_nonce

    def set_default_headers(self):
        # Security headers
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-Frame-Options", "DENY")
        self.set_header("X-XSS-Protection", "1; mode=block")
        self.set_header("Referrer-Policy", "strict-origin-when-cross-origin")
        # Note: CSP with nonce is set in prepare() after nonce generation

    def _set_csp_header(self):
        """Set CSP header with the request-specific nonce."""
        nonce = self.get_csp_nonce()
        # Use nonce for scripts, keep unsafe-inline for styles (inline style attributes are common)
        csp = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            f"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            f"img-src 'self' data:; "
            f"connect-src 'self' ws: wss:;"
        )
        self.set_header("Content-Security-Policy", csp)

    def render(self, template_name, **kwargs):
        """Override render to set CSP header before rendering."""
        self._set_csp_header()
        super().render(template_name, **kwargs)

    def get_template_namespace(self):
        """Add CSP nonce to template namespace so templates can use it."""
        namespace = super().get_template_namespace()
        namespace["csp_nonce"] = self.get_csp_nonce()
        namespace["is_feature_enabled"] = is_feature_enabled
        return namespace

    def get_current_user(self):
        return authenticate_handler(self)

    def write_error(self, status_code, **kwargs):
        # Custom error page rendering
        try:
            self.render(
                "error.html",
                status_code=status_code,
                error_message=self._reason,
            )
        except Exception:
            # Fallback if template rendering fails
            self.write(
                f"<html><body><h1>Error {status_code}</h1><p>{self._reason}</p></body></html>"
            )

    def on_finish(self):
        # Ensure cleanup logic is robust
        try:
            super().on_finish()
        except Exception:
            logger.debug("BaseHandler.on_finish cleanup failed", exc_info=True)

    def is_admin_user(self) -> bool:
        """Return True if the current user is an admin.
        Checks the user object (if provided by get_current_user) and an 'admin' secure cookie.
        """
        try:
            if hasattr(self, "get_current_admin"):
                try:
                    if self.get_current_admin():
                        return True
                except Exception:
                    logger.debug("get_current_admin check failed", exc_info=True)
            user = self.get_current_user()
            if isinstance(user, dict) and user.get("role") == "admin":
                return True
            # Note: Removed dangerous string check that matched any username containing 'admin'
            # Admin status should only be determined by role, not by username substring
        except Exception:
            logger.debug("is_admin_user role check failed", exc_info=True)
        try:
            return bool(self.get_secure_cookie("admin"))
        except Exception:
            return False

    def get_display_username(self) -> str:
        """Get username for display purposes"""
        user = self.get_current_user()
        if not user:
            return "Guest"
        if isinstance(user, dict):
            return _display_username_from_dict(user)
        return _display_username_from_legacy(user, self)
