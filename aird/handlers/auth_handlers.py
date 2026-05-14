import tornado.web
import tornado.escape
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
from aird.constants.input_limits import (
    InputTooLongError,
    LOGIN_PASSWORD_MAX_LEN,
    LOGIN_USERNAME_MAX_LEN,
    SAFE_NEXT_URL_MAX_LEN,
)
from aird.handlers.constants import (
    TOO_MANY_LOGIN_ATTEMPTS_MSG,
    INVALID_INPUT_LENGTH_MSG,
    INVALID_CREDENTIALS_MSG,
    ADMIN_URL,
    ADMIN_LOGIN_TEMPLATE,
    PROFILE_TEMPLATE,
    MANDATORY_PASSWORD_TEMPLATE,
    FILES_BASE_URL,
    LOGIN_HTML,
    DB_NOT_AVAILABLE_MSG,
)
import time
from aird.db.shares import list_shares_accessible_to_user

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


def _ldap_sync_user(db_conn, username, admin_users, user_service):
    """Create or update Aird user after LDAP auth. Return user_role for cookie."""
    is_admin = username in admin_users
    existing = user_service.get_user(db_conn, username)
    if not existing:
        try:
            role = "admin" if is_admin else "user"
            # Use a random unusable placeholder — never store the LDAP bind password.
            import secrets as _sec
            ldap_placeholder = "ldap:unset:" + _sec.token_hex(32)
            user_service.create_user(
                db_conn,
                username,
                ldap_placeholder,
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


def _apply_session_cookies(handler, username: str, user_role: str) -> None:
    """Set auth cookies, publish event, audit login (without redirect)."""
    opts = handler.session_cookie_opts()
    handler.set_secure_cookie("user", username, **opts)
    handler.set_secure_cookie("user_role", user_role, **opts)
    _publish_user_authenticated(handler, username=username, role=user_role)
    handler.get_service("audit_service").log(
        handler.db_conn, "login", username=username, ip=handler.request.remote_ip
    )


def _set_login_cookies(handler, username, user_role, redirect_url):
    """Set secure cookies and redirect."""
    _apply_session_cookies(handler, username, user_role)
    handler.redirect(redirect_url)


# ---------------------------------------------------------------------------
# Helpers for LoginHandler (username/password and token flows)
# ---------------------------------------------------------------------------


def _try_username_password_login(handler, username, password, next_url):
    """Attempt username/password login. Return True if response already sent."""
    db_conn = handler.db_conn
    if not db_conn or not username or not password:
        return False
    try:
        user = handler.get_service("user_service").authenticate(
            db_conn, username, password
        )
        auth_ok = user is not None
        if not auth_ok:
            handler.render(
                LOGIN_HTML,
                error=INVALID_CREDENTIALS_MSG,
                settings=handler.settings,
                next_url=next_url,
            )
            return True
        _apply_session_cookies(handler, username, user["role"])
        if user.get("must_change_password"):
            mq = tornado.escape.url_escape(next_url)
            handler.redirect(f"/auth/mandatory-password?next={mq}")
            return True
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
        error="Invalid access token.",
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
            if user.get("must_change_password"):
                n = tornado.escape.url_escape(ADMIN_URL)
                handler.redirect(f"/auth/mandatory-password?next={n}")
                return True
            handler.redirect(ADMIN_URL)
            return True
        if user and user["role"] != "admin":
            handler.render(
                ADMIN_LOGIN_TEMPLATE,
                error="Access denied. Admin privileges required.",
            )
            return True
        handler.render(ADMIN_LOGIN_TEMPLATE, error=INVALID_CREDENTIALS_MSG)
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
    shared_with_me: list = []
    if user and handler.db_conn:
        username = user.get("username", "") if isinstance(user, dict) else str(user)
        quota = handler.get_service("user_service").get_user_quota(
            handler.db_conn, username
        )
        shared_with_me = list_shares_accessible_to_user(handler.db_conn, username)
    handler.render(
        PROFILE_TEMPLATE,
        user=user,
        error=error,
        success=success,
        ldap_enabled=ldap_enabled,
        quota=quota,
        shared_with_me=shared_with_me,
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
        user_service.update_user(
            db_conn,
            user["id"],
            password=new_password,
            must_change_password=False,
        )
        return ("Password updated successfully", None)
    except Exception as e:
        logging.error(
            "Error updating password for user %s: %s", user.get("username"), e
        )
        return (None, "Error updating password. Please try again.")


def check_login_rate_limit(remote_ip):
    now = time.time()
    # Purge stale entries when dict grows large to prevent unbounded memory use
    if len(_LOGIN_ATTEMPTS) > 500:
        cleanup_stale_rate_limits()
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
        cu = self.current_user
        if cu:
            if isinstance(cu, dict) and cu.get("must_change_password"):
                mq = tornado.escape.url_escape(FILES_BASE_URL)
                self.redirect(f"/auth/mandatory-password?next={mq}")
                return
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
        if len(username) > LOGIN_USERNAME_MAX_LEN or len(password) > LOGIN_PASSWORD_MAX_LEN:
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
                    admin_users,
                    user_service=self.get_service("user_service"),
                )

            _apply_session_cookies(self, username, user_role)
            u_after = (
                self.get_service("user_service").get_user(db_conn, username)
                if db_conn
                else None
            )
            if u_after and u_after.get("must_change_password"):
                mq = tornado.escape.url_escape(FILES_BASE_URL)
                self.redirect(f"/auth/mandatory-password?next={mq}")
                return
            self.redirect(FILES_BASE_URL)
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
        if len(next_url) > SAFE_NEXT_URL_MAX_LEN:
            return FILES_BASE_URL
        if self._is_safe_redirect_url(next_url):
            return next_url
        return FILES_BASE_URL

    def get(self):
        user = self.current_user
        if user:
            if isinstance(user, dict) and user.get("must_change_password"):
                next_url = self._get_safe_next_url()
                mq = tornado.escape.url_escape(next_url)
                self.redirect(f"/auth/mandatory-password?next={mq}")
                return
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
        except InputTooLongError:
            self.render(
                LOGIN_HTML,
                error="Input is too long. Please shorten username, password, or token.",
                settings=self.settings,
                next_url=self._get_safe_next_url(),
            )
            return
        except Exception:
            logging.error("Error parsing login form data", exc_info=True)
            self.render(
                LOGIN_HTML,
                error="Error processing login request. Please try again.",
                settings=self.settings,
                next_url=FILES_BASE_URL,
            )
            return

        # Token takes priority: if a token is submitted, never fall through to
        # username/password (prevents autofill from shadowing an explicit token).
        if token and _try_token_login(self, token, next_url):
            return
        if (
            username
            and password
            and _try_username_password_login(self, username, password, next_url)
        ):
            return
        if username or password:
            self.render(
                LOGIN_HTML,
                error=INVALID_CREDENTIALS_MSG,
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
        cu = self.current_user
        if cu and isinstance(cu, dict) and cu.get("must_change_password"):
            n = tornado.escape.url_escape(ADMIN_URL)
            self.redirect(f"/auth/mandatory-password?next={n}")
            return
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

        try:
            auth_req = AuthRequest.from_handler(self)
        except InputTooLongError:
            self.render(
                ADMIN_LOGIN_TEMPLATE,
                error="Input is too long. Please shorten username, password, or token.",
            )
            return
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
                    ADMIN_LOGIN_TEMPLATE, error=INVALID_CREDENTIALS_MSG
                )
            else:
                self.render(
                    ADMIN_LOGIN_TEMPLATE,
                    error="Username/password or token is required.",
                )


def _mandatory_password_safe_next(next_arg: str) -> str:
    if not next_arg or len(next_arg) > SAFE_NEXT_URL_MAX_LEN:
        return FILES_BASE_URL
    if not next_arg.startswith("/") or next_arg.startswith("//"):
        return FILES_BASE_URL
    if ":" in next_arg.split("/")[0]:
        return FILES_BASE_URL
    return next_arg


class MandatoryPasswordHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        cu = self.current_user
        if not isinstance(cu, dict):
            self.redirect(FILES_BASE_URL)
            return
        row = self.get_service("user_service").get_user(self.db_conn, cu["username"])
        if not row or not row.get("must_change_password"):
            self.redirect(
                _mandatory_password_safe_next(self.get_argument("next", ""))
            )
            return
        if cu.get("username") in self._TOKEN_ONLY_USERNAMES:
            self.redirect(FILES_BASE_URL)
            return
        next_url = _mandatory_password_safe_next(self.get_argument("next", ""))
        self.render(
            MANDATORY_PASSWORD_TEMPLATE,
            error=None,
            next_url=next_url,
            username=cu.get("username", ""),
        )

    @tornado.web.authenticated
    def post(self):
        db_conn = self.db_conn
        cu = self.current_user
        if not db_conn or not isinstance(cu, dict):
            self.redirect("/login")
            return
        row = self.get_service("user_service").get_user(db_conn, cu["username"])
        if not row or not row.get("must_change_password"):
            self.redirect(FILES_BASE_URL)
            return
        if cu.get("username") in self._TOKEN_ONLY_USERNAMES:
            self.redirect(FILES_BASE_URL)
            return

        new_password = self.get_argument("new_password", "")
        confirm_password = self.get_argument("confirm_password", "")
        next_url = _mandatory_password_safe_next(self.get_argument("next", ""))
        if (
            len(new_password) > LOGIN_PASSWORD_MAX_LEN
            or len(confirm_password) > LOGIN_PASSWORD_MAX_LEN
        ):
            self.render(
                MANDATORY_PASSWORD_TEMPLATE,
                error="Password fields are too long.",
                next_url=next_url,
                username=cu.get("username", ""),
            )
            return

        if not new_password:
            self.render(
                MANDATORY_PASSWORD_TEMPLATE,
                error="Enter a new password.",
                next_url=next_url,
                username=cu.get("username", ""),
            )
            return

        is_valid, err = validate_password(new_password)
        if not is_valid:
            self.render(
                MANDATORY_PASSWORD_TEMPLATE,
                error=err,
                next_url=next_url,
                username=cu.get("username", ""),
            )
            return
        if new_password != confirm_password:
            self.render(
                MANDATORY_PASSWORD_TEMPLATE,
                error="Passwords do not match.",
                next_url=next_url,
                username=cu.get("username", ""),
            )
            return

        ok = self.get_service("user_service").update_user(
            db_conn,
            row["id"],
            password=new_password,
            must_change_password=False,
        )
        if not ok:
            self.render(
                MANDATORY_PASSWORD_TEMPLATE,
                error="Could not update password. Try again.",
                next_url=next_url,
                username=cu.get("username", ""),
            )
            return
        self.redirect(next_url)


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
        ldap_enabled = self.settings.get("ldap_server") is not None
        quota = {"quota_bytes": None, "used_bytes": 0}
        user = self.current_user
        shared_with_me: list = []
        # Token-authenticated users don't have full profile data (created_at, active, etc.)
        # so pass user=None to show the "not available" message instead of crashing.
        if isinstance(user, dict) and user.get("username", "") in (
            "token_user",
            "admin_token",
        ):
            user = None
        if user and self.db_conn:
            username = user.get("username", "") if isinstance(user, dict) else str(user)
            quota = self.get_service("user_service").get_user_quota(
                self.db_conn, username
            )
            shared_with_me = list_shares_accessible_to_user(self.db_conn, username)
        self.render(
            PROFILE_TEMPLATE,
            user=user,
            error=None,
            success=None,
            ldap_enabled=ldap_enabled,
            quota=quota,
            shared_with_me=shared_with_me,
        )

    @tornado.web.authenticated
    def post(self):
        ldap_enabled = self.settings.get("ldap_server") is not None
        # Token-authenticated users cannot change their password via this form.
        current_user = self.current_user
        if isinstance(current_user, dict) and current_user.get("username", "") in (
            "token_user",
            "admin_token",
        ):
            _profile_render(
                self,
                None,
                error="Profile management is not available for token-authenticated sessions.",
                ldap_enabled=ldap_enabled,
            )
            return

        db_conn = self.db_conn
        if not db_conn:
            _profile_render(
                self,
                current_user,
                error=DB_NOT_AVAILABLE_MSG,
                ldap_enabled=ldap_enabled,
            )
            return

        user_service = self.get_service("user_service")
        user = user_service.get_user(db_conn, current_user["username"])
        if not user:
            _profile_render(
                self,
                current_user,
                error="User not found",
                ldap_enabled=ldap_enabled,
            )
            return

        new_password = self.get_argument("new_password", "")
        confirm_password = self.get_argument("confirm_password", "")
        if (
            len(new_password) > LOGIN_PASSWORD_MAX_LEN
            or len(confirm_password) > LOGIN_PASSWORD_MAX_LEN
        ):
            _profile_render(
                self,
                self.current_user,
                error="Password fields are too long.",
                ldap_enabled=ldap_enabled,
            )
            return

        success_msg, error_msg = _do_profile_password_update(
            db_conn,
            user,
            new_password,
            confirm_password,
            user_service=user_service,
        )
        _profile_render(
            self,
            current_user,
            success=success_msg,
            error=error_msg,
            ldap_enabled=ldap_enabled,
        )
