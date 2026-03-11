import base64
import functools
import json
import secrets

import tornado.web
import tornado.websocket

import aird.config as config_module
from aird.db import get_user_by_username

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
            self.write({"error": "Database connection not available"})
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


def authenticate_handler(handler):
    """Shared authentication logic for both HTTP and WebSocket handlers.

    Checks secure cookie and Bearer token auth. Returns a user dict or None.
    The *handler* must support get_secure_cookie() and request.headers.
    """
    user_json = handler.get_secure_cookie("user")
    if user_json:
        try:
            try:
                user_data = json.loads(user_json)
                username = user_data.get("username", "")
            except (json.JSONDecodeError, TypeError):
                username = (
                    user_json.decode("utf-8")
                    if isinstance(user_json, bytes)
                    else str(user_json)
                )

            db_conn = handler.settings.get("db_conn")
            if db_conn:
                user = get_user_by_username(db_conn, username)
                if user:
                    return user
            if username == "token_authenticated":
                return {"username": "token_user", "role": "admin"}
        except Exception:
            if isinstance(user_json, bytes):
                user_str = user_json.decode("utf-8", errors="ignore")
                if user_str == "token_authenticated":
                    return {"username": "token_user", "role": "admin"}
            return None

    auth_header = handler.request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        current_access_token = config_module.ACCESS_TOKEN
        if current_access_token:
            normalized_token = token.strip()
            normalized_access_token = current_access_token.strip()
            if secrets.compare_digest(normalized_token, normalized_access_token):
                return {"username": "token_user", "role": "admin"}

    return None


class BaseHandler(tornado.web.RequestHandler):
    @property
    def db_conn(self):
        return self.settings.get("db_conn")

    @property
    def feature_flags(self):
        return self.settings.get("feature_flags", {})

    @property
    def cloud_manager(self):
        return self.settings.get("cloud_manager")

    @property
    def network_share_manager(self):
        return self.settings.get("network_share_manager")

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
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            f"style-src 'self' 'unsafe-inline'; "
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
            pass

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
                    pass
            user = self.get_current_user()
            if isinstance(user, dict) and user.get("role") == "admin":
                return True
            # Note: Removed dangerous string check that matched any username containing 'admin'
            # Admin status should only be determined by role, not by username substring
        except Exception:
            pass
        try:
            return bool(self.get_secure_cookie("admin"))
        except Exception:
            return False

    def get_display_username(self) -> str:
        """Get username for display purposes"""
        user = self.get_current_user()
        if user:
            # Handle dict user objects (from get_current_user)
            if isinstance(user, dict):
                username = user.get("username", "")
                role = user.get("role", "")

                # Handle token-authenticated users
                if username == "token_user" or username == "token_authenticated":
                    return "Admin (Token)"

                # Show role for regular users
                if role == "admin":
                    return f"{username} (Admin)"
                elif role:
                    return f"{username} (User)"
                else:
                    return username or "Guest"

            # Handle string/bytes usernames (legacy support)
            if isinstance(user, bytes):
                user_str = user.decode("utf-8", errors="ignore")
            else:
                user_str = str(user)

            # Check for token-authenticated users
            if user_str == "token_authenticated" or user_str == "authenticated":
                return "Admin (Token)"

            # Try to get role from cookie
            role_cookie = self.get_secure_cookie("user_role")
            if role_cookie:
                if isinstance(role_cookie, bytes):
                    role = role_cookie.decode("utf-8", errors="ignore")
                else:
                    role = str(role_cookie)

                if role == "admin":
                    return f"{user_str} (Admin)"
                elif role:
                    return f"{user_str} (User)"

            return user_str

        return "Guest"
