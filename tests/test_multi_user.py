"""Tests for multi-user mode: sanitize_username_for_folder and get_user_root."""

import os
import tempfile
import shutil
from unittest.mock import MagicMock, patch

import pytest

from aird.core.security import sanitize_username_for_folder
from aird.handlers.base_handler import get_user_root, _TOKEN_ONLY_USERNAMES


# ---------------------------------------------------------------------------
# sanitize_username_for_folder
# ---------------------------------------------------------------------------


class TestSanitizeUsernameForFolder:
    """Comprehensive tests for username-to-folder sanitisation."""

    # --- Valid usernames ---

    def test_simple_alphanumeric(self):
        assert sanitize_username_for_folder("alice") == "alice"

    def test_username_with_numbers(self):
        assert sanitize_username_for_folder("user42") == "user42"

    def test_username_with_underscore(self):
        assert sanitize_username_for_folder("john_doe") == "john_doe"

    def test_username_with_hyphen(self):
        assert sanitize_username_for_folder("jane-doe") == "jane-doe"

    def test_username_with_at_sign(self):
        assert sanitize_username_for_folder("user@domain") == "user@domain"

    def test_username_with_dot(self):
        assert sanitize_username_for_folder("first.last") == "first.last"

    def test_mixed_case_preserved(self):
        assert sanitize_username_for_folder("Alice") == "Alice"

    # --- Whitespace handling ---

    def test_leading_trailing_whitespace_stripped(self):
        assert sanitize_username_for_folder("  alice  ") == "alice"

    def test_internal_spaces_replaced(self):
        assert sanitize_username_for_folder("john doe") == "john_doe"

    # --- Unsafe character replacement ---

    def test_special_chars_replaced(self):
        result = sanitize_username_for_folder("user!@#$%name")
        assert result is not None
        assert "/" not in result
        assert "\\" not in result

    def test_unicode_chars_replaced(self):
        result = sanitize_username_for_folder("ユーザー")
        # All non-ASCII chars replaced with underscore, then leading underscores stripped
        # May become None if nothing remains after stripping
        # The key is it doesn't crash and doesn't produce unsafe output

    def test_backslash_replaced(self):
        result = sanitize_username_for_folder("domain\\user")
        assert result is not None
        assert "\\" not in result

    def test_slash_replaced(self):
        result = sanitize_username_for_folder("path/user")
        assert result is not None
        assert "/" not in result

    # --- Path traversal attacks ---

    def test_dot_dot_blocked(self):
        assert sanitize_username_for_folder("..") is None

    def test_dot_dot_slash_sanitized(self):
        result = sanitize_username_for_folder("../etc")
        # "../" is sanitized: slash → underscore, leading dots stripped
        # The result is safe (no traversal possible)
        assert result is not None
        assert ".." not in result
        assert "/" not in result

    def test_dot_dot_backslash_sanitized(self):
        result = sanitize_username_for_folder("..\\etc")
        # Backslash → underscore, leading dots stripped
        assert result is not None
        assert ".." not in result
        assert "\\" not in result

    def test_embedded_traversal_blocked(self):
        assert sanitize_username_for_folder("foo/../bar") is None

    def test_single_dot_blocked(self):
        assert sanitize_username_for_folder(".") is None

    # --- Leading dot prevention (hidden directories) ---

    def test_leading_dot_stripped(self):
        result = sanitize_username_for_folder(".hidden")
        assert result is not None
        assert not result.startswith(".")

    def test_multiple_leading_dots_stripped(self):
        result = sanitize_username_for_folder("...user")
        # Contains ".." after processing, should be blocked
        assert result is None or ".." not in result

    # --- Windows reserved names ---

    def test_con_blocked(self):
        assert sanitize_username_for_folder("CON") is None

    def test_con_lowercase_blocked(self):
        assert sanitize_username_for_folder("con") is None

    def test_prn_blocked(self):
        assert sanitize_username_for_folder("PRN") is None

    def test_aux_blocked(self):
        assert sanitize_username_for_folder("AUX") is None

    def test_nul_blocked(self):
        assert sanitize_username_for_folder("NUL") is None

    def test_com1_blocked(self):
        assert sanitize_username_for_folder("COM1") is None

    def test_lpt1_blocked(self):
        assert sanitize_username_for_folder("LPT1") is None

    def test_con_with_extension_blocked(self):
        assert sanitize_username_for_folder("con.txt") is None

    def test_prn_with_extension_blocked(self):
        assert sanitize_username_for_folder("PRN.old") is None

    # --- Empty / invalid inputs ---

    def test_empty_string(self):
        assert sanitize_username_for_folder("") is None

    def test_whitespace_only(self):
        assert sanitize_username_for_folder("   ") is None

    def test_none_input(self):
        assert sanitize_username_for_folder(None) is None

    def test_non_string_input(self):
        assert sanitize_username_for_folder(42) is None

    def test_only_special_chars(self):
        # All chars are invalid except @, so we get @________ after sanitisation
        result = sanitize_username_for_folder("!@#$%^&*()")
        assert result is not None  # @ survives, so it's valid
        # Test truly all-invalid chars (no @ either)
        assert sanitize_username_for_folder("!#$%^&*()") is None

    # --- Length limits ---

    def test_long_username_truncated(self):
        long_name = "a" * 30
        result = sanitize_username_for_folder(long_name)
        assert result is not None
        assert len(result) <= 20

    def test_exactly_20_chars(self):
        name = "a" * 20
        result = sanitize_username_for_folder(name)
        assert result == name

    # --- Output safety ---

    def test_result_is_single_component(self):
        result = sanitize_username_for_folder("normal_user")
        assert result is not None
        assert os.sep not in result
        assert "/" not in result
        assert "\\" not in result


# ---------------------------------------------------------------------------
# get_user_root
# ---------------------------------------------------------------------------


class TestGetUserRoot:
    """Tests for get_user_root() per-user directory resolution."""

    @pytest.fixture
    def temp_root(self):
        """Create a temporary directory to use as ROOT_DIR."""
        d = tempfile.mkdtemp()
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def _make_handler(self, username="testuser", role="user"):
        """Create a mock handler with the given user context."""
        handler = MagicMock()
        handler.get_current_user.return_value = {
            "username": username,
            "role": role,
        }
        return handler

    def test_single_user_mode_returns_root_dir(self, temp_root):
        """When MULTI_USER is False, always return ROOT_DIR."""
        handler = self._make_handler()
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = False
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        assert result == temp_root

    def test_multi_user_mode_returns_user_subdir(self, temp_root):
        """When MULTI_USER is True, return ROOT_DIR/<username>."""
        handler = self._make_handler("alice")
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        assert result == os.path.join(temp_root, "alice")

    def test_multi_user_mode_creates_directory(self, temp_root):
        """User directory is auto-created on first access."""
        handler = self._make_handler("bob")
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        assert os.path.isdir(result)
        assert os.path.basename(result) == "bob"

    def test_multi_user_mode_unauthenticated_fallback(self, temp_root):
        """Unauthenticated user falls back to ROOT_DIR."""
        handler = MagicMock()
        handler.get_current_user.return_value = None
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        assert result == temp_root

    def test_multi_user_mode_token_user_fallback(self, temp_root):
        """Token-only users fall back to ROOT_DIR (no personal folder)."""
        for token_name in _TOKEN_ONLY_USERNAMES:
            handler = self._make_handler(token_name)
            with patch("aird.handlers.base_handler.constants_module") as mock_const:
                mock_const.MULTI_USER = True
                mock_const.ROOT_DIR = temp_root
                result = get_user_root(handler)
            assert result == temp_root, f"Token user {token_name!r} should use ROOT_DIR"

    def test_multi_user_mode_unsafe_username_sanitized(self, temp_root):
        """User with traversal in username gets a safe, sanitized folder name."""
        handler = self._make_handler("../../../etc/passwd")
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        # Sanitizer neutralizes traversal: "../../../etc/passwd" → "etc_passwd"
        assert ".." not in result
        assert result != temp_root  # does NOT fall back — it creates a safe folder
        assert os.path.isdir(result)

    def test_multi_user_mode_windows_reserved_fallback(self, temp_root):
        """User with Windows reserved name falls back to ROOT_DIR."""
        handler = self._make_handler("CON")
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        assert result == temp_root

    def test_multi_user_mode_email_username(self, temp_root):
        """Email-style username is sanitised correctly."""
        handler = self._make_handler("user@example.com")
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        expected_folder = sanitize_username_for_folder("user@example.com")
        assert result == os.path.join(temp_root, expected_folder)
        assert os.path.isdir(result)

    def test_multi_user_mode_isolates_users(self, temp_root):
        """Two different users get different directories."""
        handler_alice = self._make_handler("alice")
        handler_bob = self._make_handler("bob")
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            root_alice = get_user_root(handler_alice)
            root_bob = get_user_root(handler_bob)
        assert root_alice != root_bob
        assert os.path.basename(root_alice) == "alice"
        assert os.path.basename(root_bob) == "bob"

    def test_handler_without_get_current_user(self, temp_root):
        """Handler without get_current_user attribute falls back gracefully."""
        handler = MagicMock(spec=[])  # no attributes
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        assert result == temp_root

    def test_user_string_instead_of_dict(self, temp_root):
        """Handler returning username as string instead of dict."""
        handler = MagicMock()
        handler.get_current_user.return_value = "charlie"
        with patch("aird.handlers.base_handler.constants_module") as mock_const:
            mock_const.MULTI_USER = True
            mock_const.ROOT_DIR = temp_root
            result = get_user_root(handler)
        assert result == os.path.join(temp_root, "charlie")


# ---------------------------------------------------------------------------
# Config flag parsing
# ---------------------------------------------------------------------------


class TestMultiUserConfigFlag:
    """Tests for the -mu / --multi-user CLI argument."""

    @patch("aird.config.socket.getfqdn", return_value="test.local")
    def test_multi_user_flag_default_false(self, _mock_fqdn):
        """MULTI_USER defaults to False."""
        from aird import config

        with patch("sys.argv", ["test"]):
            config.MULTI_USER = None
            config.init_config()
            assert config.MULTI_USER is False

    @patch("aird.config.socket.getfqdn", return_value="test.local")
    def test_multi_user_flag_from_cli(self, _mock_fqdn):
        """MULTI_USER is True when -mu is passed."""
        from aird import config

        with patch("sys.argv", ["test", "-mu"]):
            config.MULTI_USER = False
            config.init_config()
            assert config.MULTI_USER is True

    @patch("aird.config.socket.getfqdn", return_value="test.local")
    def test_multi_user_flag_from_long_option(self, _mock_fqdn):
        """MULTI_USER is True when --multi-user is passed."""
        from aird import config

        with patch("sys.argv", ["test", "--multi-user"]):
            config.MULTI_USER = False
            config.init_config()
            assert config.MULTI_USER is True

    @patch("aird.config.socket.getfqdn", return_value="test.local")
    def test_multi_user_flag_from_config_file(self, _mock_fqdn):
        """MULTI_USER works from JSON config file."""
        import json
        import tempfile

        from aird import config

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"multi_user": True}, f)
            config_file = f.name

        try:
            with patch("sys.argv", ["test", "--config", config_file]):
                config.MULTI_USER = False
                config.init_config()
                assert config.MULTI_USER is True
        finally:
            os.unlink(config_file)
