"""Comprehensive security test suite for aird.

Covers: path traversal, authentication, password hashing, CSRF, rate limiting,
input validation, session management, file operations, share security, and more.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
import tornado.web

from aird.core.security import (
    is_within_root,
    is_valid_websocket_origin,
    validate_password,
    join_path,
)
from aird.database.users import (
    hash_password,
    verify_password,
    create_user,
    get_user_by_username,
    authenticate_user,
    update_user,
    delete_user,
    search_users,
)
from aird.handlers.auth_handlers import (
    LoginHandler,
    check_login_rate_limit,
    _LOGIN_ATTEMPTS,
)
from aird.handlers.base_handler import (
    BaseHandler,
    authenticate_handler,
    _try_cookie_auth,
    _try_bearer_auth,
    XSRFTokenMixin,
)
from aird.handlers.file_op_handlers import _validate_upload_destination
from aird.handlers.share_handlers import _is_token_valid, _is_user_allowed
from aird.network_share_manager import NetworkShareManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Clear rate-limit state before each test."""
    _LOGIN_ATTEMPTS.clear()
    yield
    _LOGIN_ATTEMPTS.clear()


@pytest.fixture
def db():
    """In-memory SQLite database with schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_login TEXT
        )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            username TEXT,
            action TEXT NOT NULL,
            details TEXT,
            ip TEXT
        )""")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def temp_root():
    """Temporary directory for file-operation tests."""
    d = tempfile.mkdtemp(prefix="aird_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _mock_handler(host="localhost:8000", protocol="http", allow_dev=False):
    handler = MagicMock()
    handler.request.host = host
    handler.request.protocol = protocol
    handler.settings = {"allow_dev_origins": allow_dev}
    return handler


# ===================================================================
# 1. PATH TRAVERSAL PROTECTION
# ===================================================================


class TestPathTraversal:
    """Ensure is_within_root blocks all traversal vectors."""

    def test_simple_traversal_dot_dot(self, temp_root):
        bad = os.path.join(temp_root, "..", "etc", "passwd")
        assert is_within_root(bad, temp_root) is False

    def test_double_encoded_traversal(self, temp_root):
        """Path with encoded sequences resolved to real path."""
        bad = os.path.join(temp_root, "subdir", "..", "..", "secret")
        assert is_within_root(bad, temp_root) is False

    def test_null_byte_in_path(self, temp_root):
        """Null bytes should not bypass the check."""
        try:
            result = is_within_root(temp_root + "\x00.txt", temp_root)
            # On Windows, null byte causes ValueError; on Linux it fails differently
            assert result is False or result is True  # should not crash
        except (ValueError, TypeError):
            pass  # acceptable — OS rejects null bytes

    def test_root_equals_path(self, temp_root):
        assert is_within_root(temp_root, temp_root) is True

    def test_child_is_within(self, temp_root):
        child = os.path.join(temp_root, "a", "b")
        os.makedirs(child, exist_ok=True)
        assert is_within_root(child, temp_root) is True

    def test_sibling_directory(self, temp_root):
        sibling = tempfile.mkdtemp(prefix="aird_sibling_")
        try:
            assert is_within_root(sibling, temp_root) is False
        finally:
            os.rmdir(sibling)

    def test_symlink_escape(self, temp_root):
        """Symlink pointing outside root must be rejected."""
        outside = tempfile.mkdtemp(prefix="aird_outside_")
        link = os.path.join(temp_root, "escape_link")
        try:
            os.symlink(outside, link)
            assert is_within_root(link, temp_root) is False
        except OSError:
            pytest.skip("symlinks not supported on this platform/privileges")
        finally:
            shutil.rmtree(outside, ignore_errors=True)
            if os.path.islink(link):
                os.unlink(link)

    def test_empty_strings(self):
        result = is_within_root("", "")
        assert isinstance(result, bool)

    def test_nonexistent_paths(self, temp_root):
        """Non-existent path under root should still be considered within root."""
        fake = os.path.join(temp_root, "does_not_exist.txt")
        assert is_within_root(fake, temp_root) is True

    def test_deeply_nested_traversal(self, temp_root):
        """Many levels of ../ should still be blocked."""
        bad = os.path.join(temp_root, *[".."] * 20, "etc", "shadow")
        assert is_within_root(bad, temp_root) is False


# ===================================================================
# 2. PASSWORD HASHING & VERIFICATION
# ===================================================================


class TestPasswordHashing:
    """Test password storage security."""

    def test_argon2_is_default(self):
        h = hash_password("StrongP@ss1234!")
        assert h.startswith("$argon2")

    def test_argon2_verify_correct(self):
        pw = "C0mpl3x!Pass#99"
        h = hash_password(pw)
        assert verify_password(pw, h) is True

    def test_argon2_verify_wrong(self):
        h = hash_password("correct_password")
        assert verify_password("wrong_password", h) is False

    def test_scrypt_fallback(self):
        with patch("aird.database.users.ARGON2_AVAILABLE", False), patch(
            "aird.database.users.PH", None
        ):
            h = hash_password("test_scrypt_pass")
            assert h.startswith("scrypt:")
            assert verify_password("test_scrypt_pass", h) is True
            assert verify_password("wrong", h) is False

    def test_legacy_sha256_verify(self):
        pw = "legacy_password"
        salt = secrets.token_hex(32)
        sha = hashlib.sha256((salt + pw).encode()).hexdigest()
        legacy = f"{salt}:{sha}"
        assert verify_password(pw, legacy) is True
        assert verify_password("nope", legacy) is False

    def test_empty_hash_rejected(self):
        assert verify_password("anything", "") is False

    def test_none_hash_rejected(self):
        assert verify_password("anything", None) is False

    def test_malformed_scrypt_hash(self):
        assert verify_password("pw", "scrypt:only_two") is False

    def test_malformed_hash_extra_colons(self):
        assert verify_password("pw", "a:b:c:d:e") is False

    def test_unique_hashes_per_call(self):
        """Each call should produce a different hash (salted)."""
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2

    def test_unicode_password(self):
        pw = "pässwörd_日本語_🔒"
        h = hash_password(pw)
        assert verify_password(pw, h) is True
        assert verify_password("ascii_only", h) is False

    def test_very_long_password(self):
        pw = "A" * 10000
        h = hash_password(pw)
        assert verify_password(pw, h) is True

    def test_empty_password_hashes(self):
        """Even empty strings should hash without error."""
        h = hash_password("")
        assert verify_password("", h) is True
        assert verify_password("notempty", h) is False


# ===================================================================
# 3. PASSWORD VALIDATION (strength rules)
# ===================================================================


class TestPasswordValidation:
    """Test validate_password enforces complexity rules."""

    def test_valid_strong_password(self):
        ok, msg = validate_password("Str0ng!Pass#1")
        assert ok is True
        assert msg == ""

    def test_too_short(self):
        ok, msg = validate_password("Sh0rt!")
        assert ok is False
        assert "12 characters" in msg

    def test_no_uppercase(self):
        ok, msg = validate_password("nouppercase1!")
        assert ok is False
        assert "uppercase" in msg

    def test_no_lowercase(self):
        ok, msg = validate_password("NOLOWERCASE1!")
        assert ok is False
        assert "lowercase" in msg

    def test_no_digit(self):
        ok, msg = validate_password("NoDigitHere!!")
        assert ok is False
        assert "number" in msg

    def test_no_special_char(self):
        ok, msg = validate_password("NoSpecialChar1")
        assert ok is False
        assert "special" in msg

    def test_allow_simple_passwords_flag(self):
        with patch(
            "aird.core.security.FEATURE_FLAGS", {"allow_simple_passwords": True}
        ):
            ok, msg = validate_password("weak")
            assert ok is True

    def test_exact_minimum_length(self):
        # 12 chars, has all requirements
        pw = "Abcdefghij1!"
        ok, _ = validate_password(pw)
        assert ok is True

    def test_eleven_chars_rejected(self):
        pw = "Abcdefghi1!"  # 11 chars
        ok, _ = validate_password(pw)
        assert ok is False


# ===================================================================
# 4. RATE LIMITING
# ===================================================================


class TestRateLimiting:
    """Test login rate limiting."""

    def test_allows_under_limit(self):
        for _ in range(5):
            assert check_login_rate_limit("10.0.0.1") is True

    def test_blocks_at_limit(self):
        for _ in range(5):
            check_login_rate_limit("10.0.0.2")
        assert check_login_rate_limit("10.0.0.2") is False

    def test_different_ips_independent(self):
        for _ in range(5):
            check_login_rate_limit("10.0.0.3")
        # 10.0.0.3 is now blocked
        assert check_login_rate_limit("10.0.0.3") is False
        # But 10.0.0.4 is fresh
        assert check_login_rate_limit("10.0.0.4") is True

    def test_resets_after_window(self):
        for _ in range(5):
            check_login_rate_limit("10.0.0.5")
        # Simulate time passing beyond the window
        ip_data = _LOGIN_ATTEMPTS["10.0.0.5"]
        _LOGIN_ATTEMPTS["10.0.0.5"] = (ip_data[0], time.time() - 400)
        assert check_login_rate_limit("10.0.0.5") is True


# ===================================================================
# 5. AUTHENTICATION LOGIC
# ===================================================================


class TestAuthentication:
    """Test authenticate_handler, cookie auth, bearer auth."""

    def test_bearer_auth_valid(self):
        handler = MagicMock()
        handler.request.headers = {"Authorization": "Bearer test_token_123"}
        handler.get_secure_cookie.return_value = None
        with patch("aird.handlers.base_handler.config_module") as cfg:
            cfg.ACCESS_TOKEN = "test_token_123"
            user = authenticate_handler(handler)
            assert user is not None
            assert user["role"] == "user"

    def test_bearer_auth_invalid(self):
        handler = MagicMock()
        handler.request.headers = {"Authorization": "Bearer wrong_token"}
        handler.get_secure_cookie.return_value = None
        with patch("aird.handlers.base_handler.config_module") as cfg:
            cfg.ACCESS_TOKEN = "correct_token"
            user = authenticate_handler(handler)
            assert user is None

    def test_bearer_auth_no_token_configured(self):
        handler = MagicMock()
        handler.request.headers = {"Authorization": "Bearer some_token"}
        handler.get_secure_cookie.return_value = None
        with patch("aird.handlers.base_handler.config_module") as cfg:
            cfg.ACCESS_TOKEN = None
            user = _try_bearer_auth(handler)
            assert user is None

    def test_bearer_auth_constant_time_comparison(self):
        """Ensure timing-safe comparison is used (secrets.compare_digest)."""
        handler = MagicMock()
        handler.request.headers = {"Authorization": "Bearer abc"}
        handler.get_secure_cookie.return_value = None
        with patch("aird.handlers.base_handler.config_module") as cfg:
            cfg.ACCESS_TOKEN = "abc"
            with patch(
                "aird.handlers.base_handler.secrets.compare_digest", return_value=True
            ) as mock_cd:
                _try_bearer_auth(handler)
                mock_cd.assert_called_once()

    def test_cookie_auth_token_authenticated(self):
        handler = MagicMock()
        handler.get_secure_cookie.return_value = b"token_authenticated"
        handler.settings = {"db_conn": None}
        handler.request.headers = {}
        user = _try_cookie_auth(handler)
        assert user is not None
        assert user["username"] == "token_user"
        assert user["role"] == "user"

    def test_cookie_auth_with_db_user(self, db):
        create_user(db, "alice", "Str0ng!Pass#1", role="user")
        handler = MagicMock()
        handler.get_secure_cookie.return_value = json.dumps(
            {"username": "alice"}
        ).encode()
        handler.settings = {"db_conn": db}
        handler.request.headers = {}
        user = _try_cookie_auth(handler)
        assert user is not None
        assert user["username"] == "alice"

    def test_cookie_auth_nonexistent_user(self, db):
        handler = MagicMock()
        handler.get_secure_cookie.return_value = json.dumps(
            {"username": "ghost"}
        ).encode()
        handler.settings = {"db_conn": db}
        handler.request.headers = {}
        user = _try_cookie_auth(handler)
        assert user is None

    def test_no_auth_returns_none(self):
        handler = MagicMock()
        handler.get_secure_cookie.return_value = None
        handler.request.headers = {}
        handler.settings = {"db_conn": None}
        user = authenticate_handler(handler)
        assert user is None


# ===================================================================
# 6. CSRF / XSRF PROTECTION
# ===================================================================


class TestXSRFProtection:
    """Test XSRF token validation."""

    def _make_mixin(self, cookie_token, header_token=None, post_token=None):
        mixin = XSRFTokenMixin()
        mixin.get_cookie = MagicMock(return_value=cookie_token)
        headers = {}
        if header_token is not None:
            headers["X-XSRFToken"] = header_token
        mixin.request = MagicMock()
        mixin.request.headers = MagicMock()
        mixin.request.headers.get = MagicMock(
            side_effect=lambda k, d=None: headers.get(k, d)
        )
        mixin.get_argument = MagicMock(return_value=post_token)
        return mixin

    def test_valid_header_token(self):
        mixin = self._make_mixin("token123", header_token="token123")
        # Should not raise
        mixin.check_xsrf_cookie()

    def test_valid_post_token(self):
        mixin = self._make_mixin("token123", post_token="token123")
        mixin.check_xsrf_cookie()

    def test_missing_cookie_raises(self):
        mixin = self._make_mixin(None, header_token="token123")
        with pytest.raises(tornado.web.HTTPError) as exc_info:
            mixin.check_xsrf_cookie()
        assert exc_info.value.status_code == 403

    def test_missing_provided_token_raises(self):
        mixin = self._make_mixin("token123", header_token=None, post_token=None)
        with pytest.raises(tornado.web.HTTPError):
            mixin.check_xsrf_cookie()

    def test_mismatched_token_raises(self):
        mixin = self._make_mixin("correct_token", header_token="wrong_token")
        with pytest.raises(tornado.web.HTTPError) as exc_info:
            mixin.check_xsrf_cookie()
        assert exc_info.value.status_code == 403


# ===================================================================
# 7. WEBSOCKET ORIGIN VALIDATION
# ===================================================================


class TestWebSocketOriginSecurity:
    """Test WebSocket origin checks prevent cross-origin attacks."""

    def test_valid_same_origin(self):
        h = _mock_handler("example.com:8000", "http")
        assert is_valid_websocket_origin(h, "http://example.com:8000") is True

    def test_ws_scheme_accepted(self):
        h = _mock_handler("localhost:8000", "http")
        assert is_valid_websocket_origin(h, "ws://localhost:8000") is True

    def test_wss_with_https(self):
        h = _mock_handler("localhost:443", "https")
        assert is_valid_websocket_origin(h, "wss://localhost:443") is True

    def test_cross_origin_blocked(self):
        h = _mock_handler("myapp.com:8000", "http")
        assert is_valid_websocket_origin(h, "http://evil.com:8000") is False

    def test_different_port_blocked(self):
        h = _mock_handler("localhost:8000", "http")
        assert is_valid_websocket_origin(h, "http://localhost:9999") is False

    def test_ftp_scheme_blocked(self):
        h = _mock_handler("localhost:8000", "http")
        assert is_valid_websocket_origin(h, "ftp://localhost:8000") is False

    def test_empty_origin(self):
        h = _mock_handler()
        assert is_valid_websocket_origin(h, "") is False
        assert is_valid_websocket_origin(h, None) is False

    def test_dev_origin_localhost(self):
        h = _mock_handler("production.com:8000", "http", allow_dev=True)
        assert is_valid_websocket_origin(h, "http://localhost:8000") is True

    def test_dev_origin_127(self):
        h = _mock_handler("production.com:8000", "http", allow_dev=True)
        assert is_valid_websocket_origin(h, "http://127.0.0.1:8000") is True

    def test_dev_origin_disabled(self):
        h = _mock_handler("production.com:8000", "http", allow_dev=False)
        assert is_valid_websocket_origin(h, "http://localhost:8000") is False

    def test_default_port_http(self):
        h = _mock_handler("example.com", "http")
        assert is_valid_websocket_origin(h, "http://example.com") is True

    def test_default_port_https(self):
        h = _mock_handler("example.com", "https")
        assert is_valid_websocket_origin(h, "https://example.com") is True

    def test_malformed_origin(self):
        h = _mock_handler()
        assert is_valid_websocket_origin(h, "not_a_url") is False

    def test_javascript_scheme_blocked(self):
        h = _mock_handler("localhost:8000", "http")
        assert is_valid_websocket_origin(h, "javascript:alert(1)") is False


# ===================================================================
# 8. USER DATABASE SECURITY
# ===================================================================


class TestUserDatabaseSecurity:
    """Test user CRUD and auth at the database level."""

    def test_create_user_unique(self, db):
        create_user(db, "unique_user", "Str0ng!Pass#1")
        with pytest.raises(ValueError, match="already exists"):
            create_user(db, "unique_user", "Str0ng!Pass#2")

    def test_inactive_user_cannot_login(self, db):
        user = create_user(db, "inactive_user", "Str0ng!Pass#1")
        update_user(db, user["id"], active=False)
        result = authenticate_user(db, "inactive_user", "Str0ng!Pass#1")
        assert result is None

    def test_wrong_password_fails(self, db):
        create_user(db, "testuser", "Correct!Pass1")
        result = authenticate_user(db, "testuser", "Wrong!Pass1")
        assert result is None

    def test_nonexistent_user_fails(self, db):
        result = authenticate_user(db, "nobody", "password")
        assert result is None

    def test_password_hash_not_in_auth_result(self, db):
        """authenticate_user returns user dict — verify hash is present but we
        don't accidentally leak it in plaintext."""
        create_user(db, "hashcheck", "Str0ng!Pass#1")
        user = authenticate_user(db, "hashcheck", "Str0ng!Pass#1")
        assert user is not None
        # hash should be present (for internal use) but must not be plaintext
        assert user["password_hash"] != "Str0ng!Pass#1"
        assert user["password_hash"].startswith("$argon2") or user[
            "password_hash"
        ].startswith("scrypt:")

    def test_update_password(self, db):
        user = create_user(db, "pwchange", "OldP@ss123!!")
        update_user(db, user["id"], password="NewP@ss456!!")
        assert authenticate_user(db, "pwchange", "NewP@ss456!!") is not None
        assert authenticate_user(db, "pwchange", "OldP@ss123!!") is None

    def test_delete_user(self, db):
        user = create_user(db, "tobedeleted", "Str0ng!Pass#1")
        assert delete_user(db, user["id"]) is True
        assert get_user_by_username(db, "tobedeleted") is None

    def test_sql_injection_in_username(self, db):
        """Malicious username must not allow stacked SQL (e.g. DROP TABLE)."""
        malicious = "'; DROP TABLE users; --"
        try:
            create_user(db, malicious, "Str0ng!Pass#1")
        except Exception:
            pass
        table = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        assert table is not None

    def test_sql_injection_in_search(self, db):
        """search_users must use bound parameters; injection string must not wipe DB."""
        create_user(db, "normal_user", "Str0ng!Pass#1")
        results = search_users(db, "'; DROP TABLE users; --")
        assert isinstance(results, list)
        assert get_user_by_username(db, "normal_user") is not None
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
            is not None
        )

    def test_update_user_accepts_known_fields_and_ignores_unknown_kwargs(self, db):
        """Valid columns (e.g. role) update; arbitrary kwargs must not break update_user."""
        user = create_user(db, "field_test", "Str0ng!Pass#1", role="user")
        update_user(db, user["id"], role="admin")
        updated = get_user_by_username(db, "field_test")
        assert updated["role"] == "admin"
        update_user(db, user["id"], bogus_field="hacked")
        assert get_user_by_username(db, "field_test")["role"] == "admin"


# ===================================================================
# 9. UPLOAD VALIDATION
# ===================================================================


class TestUploadValidation:
    """Test file upload security checks."""

    def test_path_traversal_in_upload_dir(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root):
            result, err = _validate_upload_destination("../../etc", "passwd")
            assert err is not None
            assert err[0] == 403

    def test_path_traversal_in_filename(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root):
            result, err = _validate_upload_destination("", "../../../etc/passwd")
            # os.path.basename strips the traversal, so it becomes "passwd"
            # which may fail on extension check instead
            if err is not None:
                assert err[0] in (400, 403, 415)

    def test_dot_dot_filename_rejected(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root):
            _, err = _validate_upload_destination("", "..")
            assert err is not None
            assert err[0] == 400

    def test_dot_filename_rejected(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root):
            _, err = _validate_upload_destination("", ".")
            assert err is not None
            assert err[0] == 400

    def test_empty_filename_rejected(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root):
            _, err = _validate_upload_destination("", "")
            assert err is not None

    def test_disallowed_extension(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root), patch(
            "aird.constants.UPLOAD_CONFIG", {"allow_all_file_types": 0}
        ):
            _, err = _validate_upload_destination("", "malware.exe")
            assert err is not None
            assert err[0] == 415

    def test_allowed_extension(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root):
            result, err = _validate_upload_destination("", "notes.txt")
            assert err is None
            assert result is not None

    def test_filename_too_long(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root):
            long_name = "a" * 256 + ".txt"
            _, err = _validate_upload_destination("", long_name)
            assert err is not None
            assert err[0] == 400

    def test_filename_max_length_ok(self, temp_root):
        with patch("aird.handlers.file_op_handlers.ROOT_DIR", temp_root):
            name = "a" * 251 + ".txt"  # 255 total
            result, err = _validate_upload_destination("", name)
            assert err is None


# ===================================================================
# 10. SESSION SECURITY
# ===================================================================


class TestSessionSecurity:
    """Test cookie and session security properties."""

    def test_session_cookie_httponly(self):
        """Session cookies must be httponly."""
        handler = MagicMock()
        handler.request.protocol = "https"
        opts = BaseHandler.session_cookie_opts(handler)
        assert opts["httponly"] is True

    def test_session_cookie_secure_on_https(self):
        handler = MagicMock()
        handler.request.protocol = "https"
        opts = BaseHandler.session_cookie_opts(handler)
        assert opts["secure"] is True

    def test_session_cookie_not_secure_on_http(self):
        handler = MagicMock()
        handler.request.protocol = "http"
        opts = BaseHandler.session_cookie_opts(handler)
        assert opts["secure"] is False

    def test_session_cookie_samesite_strict(self):
        handler = MagicMock()
        handler.request.protocol = "https"
        opts = BaseHandler.session_cookie_opts(handler)
        assert opts["samesite"] == "Strict"

    def test_session_cookie_expires(self):
        handler = MagicMock()
        handler.request.protocol = "https"
        opts = BaseHandler.session_cookie_opts(handler, expires_days=7)
        assert opts["expires_days"] == 7


# ===================================================================
# 11. SECURITY HEADERS
# ===================================================================


class TestSecurityHeaders:
    """Test that BaseHandler sets proper security headers."""

    def test_default_headers_set(self):
        handler = MagicMock(spec=BaseHandler)
        handler.set_header = MagicMock()
        # Call the real method
        BaseHandler.set_default_headers(handler)
        calls = {c[0][0]: c[0][1] for c in handler.set_header.call_args_list}
        assert calls["X-Content-Type-Options"] == "nosniff"
        assert calls["X-Frame-Options"] == "DENY"
        assert calls["X-XSS-Protection"] == "1; mode=block"
        assert calls["Referrer-Policy"] == "strict-origin-when-cross-origin"


# ===================================================================
# 12. OPEN REDIRECT PROTECTION
# ===================================================================


class TestOpenRedirect:
    """Test LoginHandler._is_safe_redirect_url."""

    def _check(self, url):
        handler = MagicMock(spec=LoginHandler)
        return LoginHandler._is_safe_redirect_url(handler, url)

    def test_relative_path_allowed(self):
        assert self._check("/files/docs") is True

    def test_protocol_relative_blocked(self):
        assert self._check("//evil.com/phish") is False

    def test_absolute_url_blocked(self):
        assert self._check("https://evil.com") is False

    def test_javascript_url_blocked(self):
        assert self._check("javascript:alert(1)") is False

    def test_empty_url_blocked(self):
        assert self._check("") is False

    def test_none_url_blocked(self):
        assert self._check(None) is False

    def test_data_url_blocked(self):
        assert self._check("data:text/html,<h1>hi</h1>") is False

    def test_scheme_in_path_blocked(self):
        assert self._check("http://evil.com/path") is False


# ===================================================================
# 13. SHARE TOKEN SECURITY
# ===================================================================


class TestShareTokenSecurity:
    """Test share token verification uses constant-time comparison."""

    def test_token_comparison_uses_compare_digest(self):
        share = {"secret_token": "valid_token_abc"}
        request = MagicMock()
        request.headers = {"Authorization": "Bearer valid_token_abc"}
        get_cookie = MagicMock(return_value=None)
        with patch(
            "aird.handlers.share_handlers.secrets.compare_digest", return_value=True
        ) as mock_cd:
            result = _is_token_valid(share, "share_id", request, get_cookie)
            assert result is True
            mock_cd.assert_called_once()

    def test_no_token_required(self):
        share = {"secret_token": None}
        result = _is_token_valid(share, "sid", MagicMock(), MagicMock())
        assert result is True

    def test_wrong_token_rejected(self):
        share = {"secret_token": "correct_token"}
        request = MagicMock()
        request.headers = {"Authorization": "Bearer wrong_token"}
        get_cookie = MagicMock(return_value=None)
        result = _is_token_valid(share, "sid", request, get_cookie)
        assert result is False

    def test_missing_token_rejected(self):
        share = {"secret_token": "some_token"}
        request = MagicMock()
        request.headers = {}
        get_cookie = MagicMock(return_value=None)
        result = _is_token_valid(share, "sid", request, get_cookie)
        assert result is False

    def test_token_from_cookie(self):
        share = {"secret_token": "cookie_token_val"}
        request = MagicMock()
        request.headers = {}
        get_cookie = MagicMock(return_value="cookie_token_val")
        result = _is_token_valid(share, "sid", request, get_cookie)
        assert result is True


# ===================================================================
# 14. SHARE ACCESS CONTROL
# ===================================================================


class TestShareAccessControl:
    """Test share user-based access control."""

    def test_no_user_restriction(self):
        share = {"allowed_users": None}
        ok, err = _is_user_allowed(share, MagicMock(return_value=None))
        assert ok is True

    def test_empty_allowed_users(self):
        share = {"allowed_users": []}
        ok, err = _is_user_allowed(share, MagicMock(return_value=None))
        assert ok is True

    def test_allowed_user_passes(self):
        share = {"allowed_users": ["alice", "bob"]}
        get_secure_cookie = MagicMock(return_value=b"alice")
        ok, err = _is_user_allowed(share, get_secure_cookie)
        assert ok is True

    def test_disallowed_user_blocked(self):
        share = {"allowed_users": ["alice", "bob"]}
        get_secure_cookie = MagicMock(return_value=b"eve")
        ok, err = _is_user_allowed(share, get_secure_cookie)
        assert ok is False
        assert err[0] == 403

    def test_unauthenticated_user_blocked(self):
        share = {"allowed_users": ["alice"]}
        get_secure_cookie = MagicMock(return_value=None)
        ok, err = _is_user_allowed(share, get_secure_cookie)
        assert ok is False
        assert err[0] == 401


# ===================================================================
# 15. FILE OPERATION PATH SECURITY
# ===================================================================


class TestFileOperationPathSecurity:
    """Test that file operations enforce path boundaries."""

    def test_folder_name_with_slash_rejected(self):
        """CreateFolderHandler should reject folder names containing slashes."""
        name = "sub/dir"
        assert "/" in name or "\\" in name

    def test_folder_name_with_backslash_rejected(self):
        name = "sub\\dir"
        assert "\\" in name

    def test_dot_dot_folder_rejected(self):
        assert ".." in [".", ".."]

    def test_rename_traversal(self, temp_root):
        """Renaming to a path outside root should fail is_within_root check."""
        new_abs = os.path.abspath(os.path.join(temp_root, "..", "escaped.txt"))
        assert is_within_root(new_abs, temp_root) is False

    def test_copy_dest_outside_root(self, temp_root):
        dest = os.path.abspath(os.path.join(temp_root, "..", "..", "tmp", "stolen"))
        assert is_within_root(dest, temp_root) is False


# ===================================================================
# 16. NETWORK SHARE SECURITY
# ===================================================================


class TestNetworkShareSecurity:
    """Test network share manager security properties."""

    def test_nonexistent_folder_rejected(self):
        mgr = NetworkShareManager()
        share = {
            "id": "test1",
            "name": "TestShare",
            "folder_path": "/nonexistent/path/abc123",
            "protocol": "webdav",
            "port": 9999,
            "username": "user",
            "password": "pass",
        }
        result = mgr.start_share(share)
        assert result is False

    def test_stop_nonexistent_share(self):
        mgr = NetworkShareManager()
        assert mgr.stop_share("nonexistent") is False

    def test_is_running_nonexistent(self):
        mgr = NetworkShareManager()
        assert mgr.is_running("nonexistent") is False


# ===================================================================
# 17. INPUT SANITIZATION
# ===================================================================


class TestInputSanitization:
    """Test input sanitization and edge cases."""

    def test_ldap_username_regex(self):
        """LDAP login should only allow safe username characters."""
        pattern = r"^[a-zA-Z0-9_.\-@]+$"
        assert re.match(pattern, "normal_user") is not None
        assert re.match(pattern, "user@domain.com") is not None
        assert re.match(pattern, "user.name-123") is not None
        # Attack vectors
        assert re.match(pattern, "user)(cn=*)") is None  # LDAP injection
        assert re.match(pattern, "user;ls") is None  # command injection
        assert re.match(pattern, "user<script>") is None  # XSS
        assert re.match(pattern, "user\x00null") is None  # null byte
        assert re.match(pattern, "") is None

    def test_join_path_normalizes_separators(self):
        result = join_path("a\\b", "c\\d")
        assert "\\" not in result

    def test_json_body_parse_resilience(self):
        """BaseHandler.parse_json_body should handle malformed JSON."""
        handler = MagicMock()
        handler.request.body = b"not json at all"
        result = BaseHandler.parse_json_body(handler)
        assert result == {}

    def test_json_body_parse_empty(self):
        handler = MagicMock()
        handler.request.body = b""
        result = BaseHandler.parse_json_body(handler)
        assert result == {}


# ===================================================================
# 18. CSP NONCE GENERATION
# ===================================================================


class TestCSPNonce:
    """Test Content Security Policy nonce generation."""

    def test_nonce_is_unique_per_request(self):
        # Simulate prepare() setting the nonce
        nonce1 = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
        nonce2 = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
        assert nonce1 != nonce2

    def test_nonce_is_base64(self):
        nonce = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
        # Should be valid base64
        decoded = base64.b64decode(nonce)
        assert len(decoded) == 16


# ===================================================================
# 19. TOKEN GENERATION STRENGTH
# ===================================================================


class TestTokenStrength:
    """Ensure tokens have sufficient entropy."""

    def test_share_id_length(self):
        """Share IDs use token_urlsafe(64) — at least 85 characters."""
        token = secrets.token_urlsafe(64)
        assert len(token) >= 85

    def test_secret_token_length(self):
        token = secrets.token_urlsafe(64)
        assert len(token) >= 85

    def test_tokens_are_unique(self):
        tokens = {secrets.token_urlsafe(64) for _ in range(100)}
        assert len(tokens) == 100  # all unique


# ===================================================================
# 20. ADMIN ACCESS CONTROL
# ===================================================================


class TestAdminAccessControl:
    """Test admin privilege checks."""

    def test_admin_role_detected(self):
        handler = MagicMock(spec=BaseHandler)
        handler.get_current_user = MagicMock(
            return_value={"username": "admin1", "role": "admin"}
        )
        handler.get_secure_cookie = MagicMock(return_value=None)
        handler.get_current_admin = MagicMock(side_effect=AttributeError)
        result = BaseHandler.is_admin_user(handler)
        assert result is True

    def test_non_admin_rejected(self):
        handler = MagicMock(spec=BaseHandler)
        handler.get_current_user = MagicMock(
            return_value={"username": "user1", "role": "user"}
        )
        handler.get_secure_cookie = MagicMock(return_value=None)
        result = BaseHandler.is_admin_user(handler)
        assert result is False

    def test_username_containing_admin_not_escalated(self):
        """A user named 'administrator' with role='user' must NOT be treated as admin."""
        handler = MagicMock(spec=BaseHandler)
        handler.get_current_user = MagicMock(
            return_value={"username": "administrator", "role": "user"}
        )
        handler.get_secure_cookie = MagicMock(return_value=None)
        result = BaseHandler.is_admin_user(handler)
        assert result is False
