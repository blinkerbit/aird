import base64
import functools
import ipaddress
import json
import logging
import os
import secrets
from datetime import datetime
from typing import Any, Callable, Protocol

import tornado.escape
import tornado.web
import tornado.websocket

import aird.config as config_module
import aird.constants as constants_module
from aird.core.security import sanitize_username_for_folder
from aird.db import get_user_attributes, get_user_by_username
from aird.domain.models import (
    AccessDecision,
    AccessRequest,
    EnvironmentContext,
    ResourceAttributes,
    SubjectAttributes,
)
from aird.constants.input_limits import SAFE_NEXT_URL_MAX_LEN
from aird.handlers.constants import DB_NOT_AVAILABLE_MSG, FILES_BASE_URL
from aird.utils.util import is_feature_enabled
from aird.core.share_root import (
    creator_folder_username_from_share_field,
    login_matches_share_creator_field,
)

logger = logging.getLogger(__name__)

DISPLAY_ADMIN_TOKEN = "Admin (Token)"  # nosec B105
DISPLAY_ACCESS_TOKEN = "Access (Token)"  # nosec B105

_PARSE_JSON_MAX_UNSET = object()


def _is_corporate_ip(ip: str | None) -> bool:
    """Return True if *ip* falls within any configured CORPORATE_IP_CIDRS."""
    if not ip:
        return False
    cidrs = getattr(constants_module, "CORPORATE_IP_CIDRS", [])
    if not cidrs:
        return False
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in ipaddress.ip_network(cidr, strict=False) for cidr in cidrs)
    except ValueError:
        return False

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

    When the ``abac_engine`` feature flag is **on**, this decorator
    additionally consults the PDP for ``admin.access`` so admin routes
    are governed by the same decision logs as the rest of the system.
    With the flag off, the legacy RBAC path runs unchanged.
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
            if is_feature_enabled("abac_engine", False):
                decision = self.check_access("admin.access")
                if decision is not None and decision.is_deny:
                    if redirect_url:
                        self.redirect(redirect_url)
                    else:
                        self.set_status(deny_status)
                        self.write(deny_body)
                    return
            return method(self, *args, **kwargs)

        return wrapper

    return decorator


def require_action(action: str, resource_arg: str | None = None):
    """Decorator factory: enforces an ABAC action via the PDP.

    The decorator is a no-op when the ``abac_engine`` feature flag is
    disabled (callers fall back to the route's existing checks). When
    enabled, it builds an :class:`~aird.domain.models.AccessRequest`,
    asks the PDP, and short-circuits with HTTP 403 on deny.

    *resource_arg* names a path-like keyword argument on the wrapped
    handler method that should be treated as the resource identifier.
    Supports both sync and async handler methods.
    """
    import asyncio

    def decorator(method):
        def _resolve_resource_path(args, kwargs):
            if resource_arg and resource_arg in kwargs:
                return kwargs.get(resource_arg)
            if resource_arg and args:
                return args[0] if isinstance(args[0], str) else None
            return None

        def _check_and_deny(self, resource_path):
            """Return True (and write 403) if access is denied, else False."""
            if not is_feature_enabled("abac_engine", False):
                return False
            decision = self.check_access(action, resource_path=resource_path)
            if decision is not None and decision.is_deny:
                self.set_status(403)
                self.write({"error": "Access denied", "reason": decision.reason})
                return True
            return False

        if asyncio.iscoroutinefunction(method):
            @functools.wraps(method)
            async def async_wrapper(self, *args, **kwargs):
                resource_path = _resolve_resource_path(args, kwargs)
                if _check_and_deny(self, resource_path):
                    return None
                return await method(self, *args, **kwargs)

            return async_wrapper

        @functools.wraps(method)
        def sync_wrapper(self, *args, **kwargs):
            resource_path = _resolve_resource_path(args, kwargs)
            if _check_and_deny(self, resource_path):
                return None
            return method(self, *args, **kwargs)

        return sync_wrapper

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
                    user.pop("password_hash", None)
                    return user
            return self._token_user_from_username(handler, username)
        except Exception:
            logger.debug("CookieAuthStrategy: unexpected error parsing cookie", exc_info=True)
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

    # ------------------------------------------------------------------
    # ABAC PEP helpers
    # ------------------------------------------------------------------

    def _build_access_request(
        self,
        action: str,
        resource_path: str | None = None,
        resource_size: int | None = None,
    ) -> AccessRequest:
        user = self.get_current_user()
        if isinstance(user, dict):
            username = user.get("username", "anonymous")
            role = user.get("role", "user")
        elif isinstance(user, (bytes, str)):
            username = (
                user.decode("utf-8", "ignore") if isinstance(user, bytes) else user
            )
            role = "admin" if self.is_admin_user() else "user"
        else:
            username = "anonymous"
            role = "anonymous"

        clearance = "admin" if role == "admin" else "internal"
        extra_attrs: list[tuple[str, str]] = []
        is_managed_device = False
        try:
            db_conn = self.db_conn
            if db_conn is not None and username and username != "anonymous":
                attrs = get_user_attributes(db_conn, username)
                if attrs.get("clearance"):
                    clearance = attrs["clearance"]
                if attrs.get("managed_device", "").lower() in ("1", "true", "yes"):
                    is_managed_device = True
                for key, value in attrs.items():
                    if key in ("clearance", "managed_device"):
                        continue
                    extra_attrs.append((key, value))
        except Exception:
            logger.debug("user_attributes lookup failed", exc_info=True)

        groups: tuple[str, ...] = ()
        for key, value in extra_attrs:
            if key == "groups" and value:
                groups = tuple(g.strip() for g in value.split(",") if g.strip())
                break

        subject = SubjectAttributes(
            username=username,
            role=role,
            clearance=clearance,
            groups=groups,
            extra=tuple(extra_attrs),
        )

        ext = None
        if resource_path and "." in resource_path:
            ext = resource_path.rsplit(".", 1)[-1].lower()
        resource = ResourceAttributes(path=resource_path, extension=ext, size=resource_size)

        client_ip = getattr(self.request, "remote_ip", None)
        is_corporate_ip = _is_corporate_ip(client_ip)

        environment = EnvironmentContext(
            timestamp=datetime.now(),
            ip=client_ip,
            is_managed_device=is_managed_device,
            is_corporate_ip=is_corporate_ip,
        )
        return AccessRequest(
            subject=subject,
            action=action,
            resource=resource,
            environment=environment,
        )

    def check_access(
        self,
        action: str,
        resource_path: str | None = None,
        *,
        resource_size: int | None = None,
        raise_on_deny: bool = False,
    ) -> AccessDecision | None:
        """Evaluate *action* against the PDP.

        Returns the :class:`AccessDecision`, or ``None`` when the engine is
        disabled or unavailable. If *raise_on_deny* is true, deny decisions
        raise ``tornado.web.HTTPError(403)``.
        """
        if not is_feature_enabled("abac_engine", False):
            return None
        policy_service = self.get_service("policy_service")
        if policy_service is None:
            return None
        try:
            request = self._build_access_request(action, resource_path, resource_size)
            audit = is_feature_enabled("abac_audit_decisions", True)
            decision = policy_service.evaluate(
                self.db_conn, request, audit=audit
            )
        except Exception:
            logger.debug("PDP evaluation failed", exc_info=True)
            return None
        if decision.is_deny and raise_on_deny:
            raise tornado.web.HTTPError(403, decision.reason)
        return decision

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

    def handle_cloud_error(self, exc: Exception, log_msg: str, client_err_msg: str) -> None:
        """Helper to unify exception handling for cloud routes."""
        from aird.cloud import CloudProviderError
        if isinstance(exc, CloudProviderError):
            self.set_status(400)
            self.write({"error": str(exc)})
        else:
            logging.exception(log_msg)
            self.set_status(500)
            self.write({"error": client_err_msg})

    def session_cookie_opts(self, expires_days=1) -> dict:
        """Return common kwargs for secure session cookies (httponly, secure, samesite, expires_days)."""
        return {
            "httponly": True,
            "secure": self.request.protocol == "https",
            "samesite": "Strict",
            "expires_days": expires_days,
        }

    def parse_json_body(self, default=None, *, max_bytes: int | None | object = _PARSE_JSON_MAX_UNSET):
        """Parse request body as JSON.

        By default enforces ``DEFAULT_JSON_BODY_MAX_BYTES``.

        Pass ``max_bytes=None`` to disable the size check (caller must validate).
        """
        from aird.constants.input_limits import DEFAULT_JSON_BODY_MAX_BYTES

        if default is None:
            default = {}
        if max_bytes is _PARSE_JSON_MAX_UNSET:
            limit: int | None = DEFAULT_JSON_BODY_MAX_BYTES
        else:
            limit = max_bytes  # type: ignore[assignment]
        raw = self.request.body or b"{}"
        if limit is not None and isinstance(raw, bytes) and len(raw) > limit:
            raise tornado.web.HTTPError(413, reason="JSON request body too large")
        try:
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            return json.loads(text)
        except json.JSONDecodeError:
            return default

    def enforce_content_length_max(self, max_bytes: int) -> None:
        """Reject POST/PUT/PATCH when Content-Length exceeds *max_bytes*."""
        if self.request.method not in ("POST", "PUT", "PATCH"):
            return
        cl = self.request.headers.get("Content-Length")
        if not cl:
            return
        try:
            n = int(cl)
        except ValueError:
            return
        if n > max_bytes:
            raise tornado.web.HTTPError(413, reason="Request body too large")

    def _maybe_redirect_mandatory_password(self):
        if self.request.method == "OPTIONS":
            return
        path = self.request.path
        if path.startswith("/auth/mandatory-password"):
            return
        if path in ("/logout", "/login", "/admin/login", "/health"):
            return
        if path.startswith("/static/"):
            return
        if path.startswith("/p2p"):
            return
        if path.startswith("/shared/"):
            return

        user = getattr(self, "current_user", None)
        if not isinstance(user, dict) or not user.get("must_change_password"):
            return
        if user.get("username") in BaseHandler._TOKEN_ONLY_USERNAMES:
            return

        dest = path
        if self.request.query:
            dest += "?" + self.request.query
        next_dest = FILES_BASE_URL
        if (
            len(dest) <= SAFE_NEXT_URL_MAX_LEN
            and dest.startswith("/")
            and not dest.startswith("//")
            and ":" not in dest.split("/")[0]
        ):
            next_dest = dest
        self.redirect(
            "/auth/mandatory-password?next=" + tornado.escape.url_escape(next_dest)
        )

    def prepare(self):
        """Generate a unique nonce for this request for CSP."""
        # Generate a cryptographically secure random nonce (16 bytes = 128 bits)
        self._csp_nonce = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")

        from aird.constants.input_limits import (
            ADMIN_HTML_FORM_MAX_BYTES,
            LOGIN_FORM_MAX_BYTES,
            PROFILE_FORM_MAX_BYTES,
        )

        if self.request.method in ("POST", "PUT", "PATCH"):
            p = self.request.path
            if p in ("/login", "/admin/login"):
                self.enforce_content_length_max(LOGIN_FORM_MAX_BYTES)
            elif p in ("/profile", "/auth/mandatory-password"):
                self.enforce_content_length_max(PROFILE_FORM_MAX_BYTES)
            elif p.startswith("/admin/") and "/api/" not in p:
                self.enforce_content_length_max(ADMIN_HTML_FORM_MAX_BYTES)

        self._maybe_redirect_mandatory_password()

    def get_csp_nonce(self):
        """Return the CSP nonce for this request."""
        if not hasattr(self, "_csp_nonce"):
            self._csp_nonce = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
        return self._csp_nonce

    def set_default_headers(self):
        # Security headers
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-Frame-Options", "DENY")
        self.set_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.set_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        if getattr(self.request, "protocol", None) == "https":
            self.set_header(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        # Note: CSP with nonce is set in render() after nonce generation

    def _set_csp_header(self):
        """Set CSP header with the request-specific nonce."""
        nonce = self.get_csp_nonce()
        # Use nonce for scripts, keep unsafe-inline for styles (inline style attributes are common)
        csp = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            f"style-src 'self' 'unsafe-inline'; "
            f"font-src 'self' data:; "
            f"img-src 'self' data:; "
            f"connect-src 'self'; "
            f"frame-ancestors 'none'; "
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
        # _app_nav_header.html expects these; missing keys raise when Super Search link renders.
        namespace.setdefault("nav_search_path", "")
        namespace.setdefault("nav_title", "")
        namespace.setdefault("show_admin_link", False)
        namespace.setdefault("ldap_enabled", self.settings.get("ldap_server") is not None)
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
            safe = tornado.escape.xhtml_escape(str(self._reason or ""))
            self.write(
                f"<html><body><h1>Error {status_code}</h1><p>{safe}</p></body></html>"
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

    def can_manage_share_secrets(self, share: dict) -> bool:
        """True if current user may view raw secret tokens and full management details."""
        if self.is_admin_user():
            return True
        u = get_username_string_for_db(self)
        if not u:
            return False
        creator = (share.get("created_by") or "").strip()
        if creator and login_matches_share_creator_field(creator, u):
            return True
        modify_users = share.get("modify_users") or []
        return u in modify_users
