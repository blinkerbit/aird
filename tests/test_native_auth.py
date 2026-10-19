"""Native login token, bearer CSRF skip, and JSON error bodies."""

from unittest.mock import MagicMock, patch

import pytest
import tornado.web

from aird.handlers.auth_handlers import ApiLoginHandler
from aird.handlers.base_handler import BaseHandler
from tests.handler_helpers import prepare_handler


def _handler(cls=BaseHandler):
    app = MagicMock()
    app.settings = {"cookie_secret": "test", "xsrf_cookies": True}
    req = MagicMock()
    req.headers = {}
    req.protocol = "http"
    req.body = b""
    req.remote_ip = "127.0.0.1"
    return prepare_handler(cls(app, req))


def test_bearer_session_authenticates_as_the_user():
    handler = _handler()
    handler.request.headers = {"Authorization": "Bearer sess-1"}
    handler.application.settings["db_conn"] = MagicMock()
    user_row = {"username": "alice", "role": "user"}
    with patch.object(handler, "get_secure_cookie", return_value=None), patch(
        "aird.handlers.base_handler.config_module.ACCESS_TOKEN", "other-token"
    ), patch(
        "aird.db.sessions.get_session",
        return_value={"id": "sess-1", "username": "alice"},
    ), patch("aird.db.sessions.touch_session"), patch(
        "aird.handlers.base_handler.get_user_by_username", return_value=user_row
    ):
        user = handler.get_current_user()
    assert user["username"] == "alice"
    assert user["_session_id"] == "sess-1"


def test_valid_bearer_skips_xsrf_and_junk_bearer_does_not():
    handler = _handler()
    handler.request.method = "POST"
    handler.request.headers = {"Authorization": "Bearer nope"}
    with patch("aird.handlers.base_handler.bearer_authenticates", return_value=True):
        handler.check_xsrf_cookie()
    with patch("aird.handlers.base_handler.bearer_authenticates", return_value=False):
        with pytest.raises(tornado.web.HTTPError):
            handler.check_xsrf_cookie()


def test_plain_error_becomes_json_when_requested():
    handler = _handler()
    del handler.write
    handler._status_code = 403
    handler.request.headers = {"Accept": "application/json"}
    with patch("tornado.web.RequestHandler.write") as parent_write:
        BaseHandler.write(handler, "Access denied")
    parent_write.assert_called_once_with({"error": "Access denied"})


def test_api_login_returns_a_session_token():
    handler = _handler(ApiLoginHandler)
    handler.request.body = b'{"username":"alice","password":"secret"}'
    handler.application.settings["db_conn"] = MagicMock()
    with patch(
        "aird.handlers.auth_handlers.check_login_rate_limit", return_value=True
    ), patch.object(
        handler.get_service("user_service"),
        "authenticate",
        return_value={"username": "alice", "role": "user", "must_change_password": False},
    ), patch(
        "aird.handlers.auth_handlers.establish_server_session", return_value="sess-9"
    ), patch("aird.handlers.auth_handlers._publish_user_authenticated"):
        handler.post()
    body = handler.write.call_args[0][0]
    assert body["token"] == "sess-9"
    assert body["username"] == "alice"
    assert body["must_change_password"] is False
