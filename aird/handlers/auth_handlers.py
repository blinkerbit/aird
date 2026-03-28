import tornado.web
import logging
from aird.handlers.base_handler import BaseHandler
from datetime import datetime
import secrets
from ldap3 import Server, Connection
from ldap3.utils.dn import escape_rdn
from ldap3.utils.conv import escape_filter_chars
from aird.core.security import validate_password
from aird.core.events import UserAuthenticatedEvent, now_ts
from aird.domain.contracts import AuthRequest
import aird.config as config_module
import aird.constants as constants_module
from aird.handlers.constants import (
    TOO_MANY_LOGIN_ATTEMPTS_MSG,
    INVALID_INPUT_LENGTH_MSG,
    INVALID_USERNAME_OR_PASSWORD_MSG,
    ADMIN_URL,
    ADMIN_LOGIN_TEMPLATE,
    PROFILE_TEMPLATE,
    FILES_BASE_URL,
    LOGIN_HTML,
)
import time

# IP -> (attempts, timestamp)
_LOGIN_ATTEMPTS = {}


def _publish_user_authenticated(handler, username: str, role: str) -> None:
    if hasattr(handler, "publish_event"):
        handler.publish_event(
            UserAuthenticatedEvent(
                username=username,
                role=role,
                ip=getattr(handler.request, "remote_ip", "") or "",
                authenticated_at=now_ts(),
            )
        )


def cleanup_stale_rate_limits():
    """Remove expired entries from the rate-limit dict to prevent unbounded growth."""
    now = time.time()
    stale = [
        ip
        for ip, (_, ts) in _LOGIN_ATTEMPTS.items()
        if now - ts > constants_module.LOGIN_RATE_LIMIT_WINDOW
    ]
    for ip in stale:
        _LOGIN_ATTEMPTS.pop(ip, None)


# ---------------------------------------------------------------------------
# Helpers for LDAP login (reduce cognitive complexity)
# ---------------------------------------------------------------------------


def _ldap_authorized(conn, ldap_attribute_map):
    """Check LDAP attribute maps for authorization. Return True if authorized."""
    if not ldap_attribute_map:
        return True
    for attribute_element in ldap_attribute_map:
        for key, value in attribute_element.items():
            try:
                if value in conn.entries[0][key]:
                    return True
            except KeyError:
                continue
    return False


def _ldap_sync_user(db_conn, username, password, admin_users, user_service):
    """Create or update Aird user after LDAP auth. Return user_role for cookie."""
    is_admin = username in admin_users
    existing = user_service.get_user(db_conn, username)
    if not existing:
        try:
            role = "admin" if is_admin else "user"
            user_service.create_user(
                db_conn,
                username,
                password,
                role=role,
            )
            logging.info(
                "LDAP: Created new user %r from LDAP with role %r", username, role
            )
        except Exception as e:
            logging.warning(
                "LDAP: Failed to create user %r in database: %s", username, e
            )
        return "admin" if is_admin else "user"
    try:
        user_service.update_user(
            db_conn,
            existing["id"],
            last_login=datetime.now().isoformat(),
        )
        logging.info("LDAP: Updated last login for user %r", username)
        if is_admin and existing["role"] != "admin":
            user_service.update_user(
                db_conn,
                existing["id"],
                role="admin",
            )
            logging.info("LDAP: Assigned admin privileges to user %r", username)
    except Exception as e:
        logging.warning("LDAP: Failed to update user %r: %s", username, e)
    role_row = user_service.get_user(db_conn, username)
    default_role = "admin" if is_admin else "user"
    return role_row["role"] if role_row else default_role


def _set_login_cookies(handler, username, user_role, redirect_url):
    """Set secure cookies and redirect."""
    opts = handler.session_cookie_opts()
    handler.set_secure_cookie("user", username, **opts)
    handler.set_secure_cookie("user_role", user_role, **opts)
    _publish_user_authenticated(handler, username=username, role=user_role)
    handler.get_service("audit_service").log(
        handler.db_conn, "login", username=username, ip=handler.request.remote_ip
    )
    handler.redirect(redirect_url)


# ---------------------------------------------------------------------------
# Helpers for LoginHandler (username/password and token flows)
# ---------------------------------------------------------------------------


def _try_username_password_login(handler, username, password, next_url):
    """Attempt username/password login. Return True if response already sent."""
    db_conn = handler.db_conn
    if not db_conn or not username or not password:
        return False
    if len(username) > 256 or len(password) > 256:
        handler.render(
            LOGIN_HTML,
            error=INVALID_INPUT_LENGTH_MSG,
            settings=handler.settings,
            next_url=next_url,
        )
        return True
    try:
        user = handler.get_service("user_service").authenticate(
            db_conn, username, password
        )
        auth_ok = user is not None
        if not auth_ok:
            handler.render(
                LOGIN_HTML,
                error=INVALID_USERNAME_OR_PASSWORD_MSG,
                settings=handler.settings,
                next_url=next_url,
            )
            return True
        handler.get_service("audit_service").log(
            db_conn, "login", username=username, ip=handler.request.remote_ip
        )
        opts = handler.session_cookie_opts()
        handler.set_secure_cookie("user", username, **opts)
        handler.set_secure_cookie("user_role", user["role"], **opts)
        _publish_user_authenticated(handler, username=username, role=user["role"])
        handler.redirect(next_url)
        return True
    except Exception as e:
        logging.error(
            "Exception during username/password authentication: %s", e, exc_info=True
        )
        handler.render(
            LOGIN_HTML,
            error="Authentication failed. Please try again.",
            settings=handler.settings,
            next_url=next_url,
        )
        return True


def _try_token_login(handler, token, next_url):
    """Attempt token login. Return True if response already sent."""
    if not token:
        return False
    if len(token) > 512:
        logging.warning("Token authentication failed.")
        handler.render(
            LOGIN_HTML,
            error="Invalid token.",
            settings=handler.settings,
            next_url=next_url,
        )
        return True
    current = config_module.ACCESS_TOKEN
    if not current:
        handler.render(
            LOGIN_HTML,
            error="Token authentication is not configured.",
            settings=handler.settings,
            next_url=next_url,
        )
        return True
    norm_token = token.strip()
    norm_access = current.strip()
    if secrets.compare_digest(norm_token, norm_access):
        handler.get_service("audit_service").log(
            handler.db_conn,
            "login",
            username="token_authenticated",
            ip=handler.request.remote_ip,
        )
        opts = handler.session_cookie_opts()
        handler.set_secure_cookie("user", "token_authenticated", **opts)
        handler.set_secure_cookie("user_role", "user", **opts)
        _publish_user_authenticated(
            handler, username="token_authenticated", role="user"
        )
        handler.redirect(next_url)
        return True
    logging.warning("Token authentication failed. Token mismatch.")
    handler.render(
        LOGIN_HTML,
        error="Invalid credentials. Try again.",
        settings=handler.settings,
        next_url=next_url,
    )
    return True


# ---------------------------------------------------------------------------
# Helpers for AdminLoginHandler
# ---------------------------------------------------------------------------


def _try_admin_username_password_login(handler, username, password):
    """Attempt admin username/password login. Return True if response sent."""
    db_conn = handler.db_conn
    if not db_conn or not username or not password:
        return False
    if len(username) > 256 or len(password) > 256:
        handler.render(ADMIN_LOGIN_TEMPLATE, error=INVALID_INPUT_LENGTH_MSG)
        return True
    try:
        user = handler.get_service("user_service").authenticate(
            db_conn, username, password
        )
        if user and user["role"] == "admin":
            handler.get_service("audit_service").log(
                db_conn,
                "admin_login",
                username=username,
                ip=handler.request.remote_ip,
            )
            opts = handler.session_cookie_opts()
            handler.set_secure_cookie("user", username, **opts)
            handler.set_secure_cookie("user_role", user["role"], **opts)
            handler.set_secure_cookie("admin", "authenticated", **opts)
            _publish_user_authenticated(handler, username=username, role=user["role"])
            handler.redirect(ADMIN_URL)
            return True
        if user and user["role"] != "admin":
            handler.render(
                ADMIN_LOGIN_TEMPLATE,
                error="Access denied. Admin privileges required.",
            )
            return True
        handler.render(ADMIN_LOGIN_TEMPLATE, error=INVALID_USERNAME_OR_PASSWORD_MSG)
        return True
    except Exception:
        handler.render(
            ADMIN_LOGIN_TEMPLATE,
            error="Authentication failed. Please try again.",
        )
        return True


def _try_admin_token_login(handler, token):
    """Attempt admin token login. Return True if response sent."""
    if not token:
        return False
    if len(token) > 512:
        handler.render(ADMIN_LOGIN_TEMPLATE, error="Invalid token.")
        return True
    current = config_module.ADMIN_TOKEN
    if not current:
        handler.render(
            ADMIN_LOGIN_TEMPLATE,
            error="Admin token authentication is not configured.",
        )
        return True
    norm_token = token.strip()
    norm_admin = current.strip()
    if secrets.compare_digest(norm_token, norm_admin):
        handler.get_service("audit_service").log(
            handler.db_conn,
            "admin_login",
            username="admin_token",
            ip=handler.request.remote_ip,
        )
        opts = handler.session_cookie_opts()
        # Must set user cookie so @authenticated passes; admin cookie for is_admin_user
        handler.set_secure_cookie("user", "admin_token_authenticated", **opts)
        handler.set_secure_cookie("user_role", "admin", **opts)
        handler.set_secure_cookie("admin", "authenticated", **opts)
        _publish_user_authenticated(
            handler, username="admin_token_authenticated", role="admin"
        )
        handler.redirect(ADMIN_URL)
        return True
    handler.render(ADMIN_LOGIN_TEMPLATE, error="Invalid admin token.")
    return True


# ---------------------------------------------------------------------------
# Helpers for ProfileHandler
# ---------------------------------------------------------------------------


def _profile_render(handler, user, error=None, success=None, ldap_enabled=None):
    """Render profile template with common kwargs."""
    if ldap_enabled is None:
        ldap_enabled = handler.settings.get("ldap_server") is not None
    quota = {"quota_bytes": None, "used_bytes": 0}
    if user and handler.db_conn:
        username = user.get("username", "") if isinstance(user, dict) else str(user)
        quota = handler.get_service("user_service").get_user_quota(
            handler.db_conn, username
        )
    handler.render(
        PROFILE_TEMPLATE,
        user=user,
        error=error,
        success=success,
        ldap_enabled=ldap_enabled,
        quota=quota,
    )


def _do_profile_password_update(
    db_conn, user, new_password, confirm_password, user_service
):
    """Validate and perform password update. Return (success_msg, None) or (None, error_msg)."""
    if not new_password:
        return (None, None)
    if new_password != confirm_password:
        return (None, "Passwords do not match")
    is_valid, error_msg = validate_password(new_password)
    if not is_valid:
        return (None, error_msg)
    try:
        user_service.update_user(db_conn, user["id"], password=new_password)
        return ("Password updated successfully", None)
    except Exception as e:
        logging.error(
            "Error updating password for user %s: %s", user.get("username"), e
        )
        return (None, "Error updating password. Please try again.")


def check_login_rate_limit(remote_ip):
    now = time.time()
    attempts, timestamp = _LOGIN_ATTEMPTS.get(remote_ip, (0, now))

    if now - timestamp > constants_module.LOGIN_RATE_LIMIT_WINDOW:
        attempts = 0
        timestamp = now

    if attempts >= constants_module.LOGIN_RATE_LIMIT_ATTEMPTS:
        return False

    _LOGIN_ATTEMPTS[remote_ip] = (attempts + 1, timestamp)
    return True


class LDAPLoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            self.redirect(FILES_BASE_URL)
            return
        self.render(LOGIN_HTML, error=None, settings=self.settings)

    def post(self):
        if not check_login_rate_limit(self.request.remote_ip):
            self.set_status(429)
            self.render(
                LOGIN_HTML,
                error=TOO_MANY_LOGIN_ATTEMPTS_MSG,
                settings=self.settings,
            )
            return

        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "")

        if not username or not password:
            self.render(
                LOGIN_HTML,
                error="Username and password are required.",
                settings=self.settings,
            )
            return
        if len(username) > 256 or len(password) > 256:
            self.render(
                LOGIN_HTML, error=INVALID_INPUT_LENGTH_MSG, settings=self.settings
            )
            return

        import re

        if not re.match(r"^[a-zA-Z0-9_.\-@]+$", username):
            self.render(
                LOGIN_HTML,
                error="Invalid username format.",
                settings=self.settings,
            )
            return

        try:
            server = Server(self.settings["ldap_server"])
            username_dn_escaped = escape_rdn(username)
            conn = Connection(
                server,
                user=self.settings["ldap_user_template"].format(
                    username=username_dn_escaped
                ),
                password=password,
                auto_bind=True,
            )
            username_escaped = escape_filter_chars(username)
            conn.search(
                search_base=self.settings["ldap_base_dn"],
                search_filter=self.settings["ldap_filter_template"].format(
                    username=username_escaped
                ),
                attributes=self.settings["ldap_attributes"],
            )

            if not _ldap_authorized(conn, self.settings.get("ldap_attribute_map", [])):
                self.render(
                    LOGIN_HTML,
                    error="Access denied. You do not have permission to access this system.",
                    settings=self.settings,
                )
                return

            user_role = "user"
            db_conn = self.db_conn
            if db_conn:
                admin_users = self.settings.get("admin_users", [])
                user_role = _ldap_sync_user(
                    db_conn,
                    username,
                    password,
                    admin_users,
                    user_service=self.get_service("user_service"),
                )

            _set_login_cookies(self, username, user_role, FILES_BASE_URL)
        except Exception:
            self.render(
                LOGIN_HTML,
                error="Authentication failed. Please check your credentials.",
                settings=self.settings,
            )


class LoginHandler(BaseHandler):
    def _is_safe_redirect_url(self, url: str) -> bool:
        """Validate that a redirect URL is safe (relative path only, no external redirects)."""
        if not url:
            return False
        # Must start with / and not with // (protocol-relative URL)
        if not url.startswith("/") or url.startswith("//"):
            return False
        # Block URLs with protocol schemes
        if ":" in url.split("/")[0]:
            return False
        return True

    def _get_safe_next_url(self) -> str:
        """Get a validated next URL, defaulting to /files/ if invalid."""
        next_url = self.get_argument("next", FILES_BASE_URL)
        if self._is_safe_redirect_url(next_url):
            return next_url
        return FILES_BASE_URL

    def get(self):
        if self.current_user:
            # Already logged in, redirect to intended destination or files page
            next_url = self._get_safe_next_url()
            logging.info("User already authenticated, redirecting to safe URL")
            self.redirect(next_url)
            return
        # Not logged in, show login form with next URL preserved
        next_url = self._get_safe_next_url()
        logging.debug("Showing login form")
        self.render(LOGIN_HTML, error=None, settings=self.settings, next_url=next_url)

    def post(self):
        if not check_login_rate_limit(self.request.remote_ip):
            self.set_status(429)
            self.render(
                LOGIN_HTML,
                error=TOO_MANY_LOGIN_ATTEMPTS_MSG,
                settings=self.settings,
                next_url=self._get_safe_next_url(),
            )
            return

        try:
            auth_req = AuthRequest.from_handler(self)
            username = auth_req.username
            password = auth_req.password
            token = auth_req.token
            next_url = self._get_safe_next_url()
        except Exception:
            logging.error("Error parsing login form data", exc_info=True)
            self.render(
                LOGIN_HTML,
                error="Error processing login request. Please try again.",
                settings=self.settings,
                next_url=FILES_BASE_URL,
            )
            return

        if (
            username
            and password
            and _try_username_password_login(self, username, password, next_url)
        ):
            return
        if token and _try_token_login(self, token, next_url):
            return
        if not token:
            if username or password:
                self.render(
                    LOGIN_HTML,
                    error=INVALID_USERNAME_OR_PASSWORD_MSG,
                    settings=self.settings,
                    next_url=next_url,
                )
            else:
                self.render(
                    LOGIN_HTML,
                    error="Username/password or token is required.",
                    settings=self.settings,
                    next_url=next_url,
                )


class AdminLoginHandler(BaseHandler):
    def get(self):
        if self.is_admin_user():
            self.redirect(ADMIN_URL)
            return
        self.render(ADMIN_LOGIN_TEMPLATE, error=None)

    def post(self):
        if not check_login_rate_limit(self.request.remote_ip):
            self.set_status(429)
            self.render(
                ADMIN_LOGIN_TEMPLATE,
                error=TOO_MANY_LOGIN_ATTEMPTS_MSG,
            )
            return

        auth_req = AuthRequest.from_handler(self)
        username = auth_req.username
        password = auth_req.password
        token = auth_req.token

        if (
            username
            and password
            and _try_admin_username_password_login(self, username, password)
        ):
            return
        if token and _try_admin_token_login(self, token):
            return
        if not token:
            if username or password:
                self.render(
                    ADMIN_LOGIN_TEMPLATE, error=INVALID_USERNAME_OR_PASSWORD_MSG
                )
            else:
                self.render(
                    ADMIN_LOGIN_TEMPLATE,
                    error="Username/password or token is required.",
                )


class LogoutHandler(BaseHandler):
    def get(self):
        # Clear all auth cookies
        self.clear_cookie("user")
        self.clear_cookie("user_role")
        self.clear_cookie("admin")
        # Redirect to login page
        self.redirect("/login")


class ProfileHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        # Check if LDAP is enabled
        ldap_enabled = self.settings.get("ldap_server") is not None
        quota = {"quota_bytes": None, "used_bytes": 0}
        user = self.current_user
        if user and self.db_conn:
            username = user.get("username", "") if isinstance(user, dict) else str(user)
            quota = self.get_service("user_service").get_user_quota(
                self.db_conn, username
            )
        self.render(
            PROFILE_TEMPLATE,
            user=user,
            error=None,
            success=None,
            ldap_enabled=ldap_enabled,
            quota=quota,
        )

    @tornado.web.authenticated
    def post(self):
        ldap_enabled = self.settings.get("ldap_server") is not None
        db_conn = self.db_conn
        if not db_conn:
            _profile_render(
                self,
                self.current_user,
                error="Database connection not available",
                ldap_enabled=ldap_enabled,
            )
            return

        user = self.get_service("user_service").get_user(
            db_conn, self.current_user["username"]
        )
        if not user:
            _profile_render(
                self,
                self.current_user,
                error="User not found",
                ldap_enabled=ldap_enabled,
            )
            return

        new_password = self.get_argument("new_password", "")
        confirm_password = self.get_argument("confirm_password", "")
        success_msg, error_msg = _do_profile_password_update(
            db_conn,
            user,
            new_password,
            confirm_password,
            user_service=self.get_service("user_service"),
        )
        _profile_render(
            self,
            self.current_user,
            success=success_msg,
            error=error_msg,
            ldap_enabled=ldap_enabled,
        )
