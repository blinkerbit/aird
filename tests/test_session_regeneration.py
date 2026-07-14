from unittest.mock import MagicMock, PropertyMock, patch

from aird.handlers.auth_handlers import _apply_session_cookies
from aird.handlers.base_handler import BaseHandler

from tests.handler_helpers import prepare_handler, _default_services


class TestRegenerateSession:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "xsrf_cookies": True,
            "services": _default_services(),
        }
        self.mock_request.protocol = "http"

    def test_regenerate_session_clears_auth_and_xsrf(self):
        handler = prepare_handler(BaseHandler(self.mock_app, self.mock_request))
        handler._xsrf_token = b"stale"
        handler._raw_xsrf_token = (1, b"stale", 0.0)

        with patch.object(handler, "clear_cookie") as mock_clear, patch.object(
            BaseHandler, "xsrf_token", new_callable=PropertyMock
        ) as mock_xsrf:
            mock_xsrf.return_value = b"new-token"
            handler.regenerate_session()

        cleared = [c[0][0] for c in mock_clear.call_args_list]
        assert cleared == ["user", "user_role", "admin", "_xsrf"]
        assert not hasattr(handler, "_xsrf_token")
        assert not hasattr(handler, "_raw_xsrf_token")
        mock_xsrf.assert_called_once()

    def test_apply_session_cookies_regenerates_before_setting(self):
        handler = prepare_handler(BaseHandler(self.mock_app, self.mock_request))
        call_order = []

        def track_regenerate():
            call_order.append("regenerate")

        def track_set(*args, **kwargs):
            call_order.append(f"set:{args[0]}")

        with patch.object(handler, "regenerate_session", side_effect=track_regenerate), patch.object(
            handler, "set_secure_cookie", side_effect=track_set
        ), patch.object(handler, "get_service", return_value=MagicMock()), patch.object(
            handler, "publish_event"
        ):
            _apply_session_cookies(handler, "alice", "user")

        assert call_order[0] == "regenerate"
        assert call_order[1:] == ["set:user", "set:user_role"]
