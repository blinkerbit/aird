"""Tests for WebAuthn handlers (feature-gated, optional)."""

import json
from unittest.mock import MagicMock, patch

import aird.constants as constants
from aird.db.schema import init_db
from aird.handlers.webauthn_handlers import (
    WebAuthnAuthOptionsHandler,
    WebAuthnRegisterOptionsHandler,
    WebAuthnStatusHandler,
)
from aird.utils.util import invalidate_feature_flags_cache
from tests.handler_helpers import authenticate, patch_db_conn, prepare_handler, _default_services


def _sqlite_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def _response_body(handler):
    write_mock = handler.write
    if write_mock.called:
        args = write_mock.call_args[0]
        if args:
            return args[0]
    chunks = getattr(handler, "_write_buffer", None) or []
    if chunks:
        return b"".join(chunks).decode("utf-8")
    return ""


class TestWebAuthnStatusHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "services": _default_services(),
        }
        self.mock_request.host = "localhost:8888"
        self.mock_request.protocol = "http"

    def teardown_method(self):
        constants.FEATURE_FLAGS["webauthn"] = False
        invalidate_feature_flags_cache()

    def test_status_404_when_disabled(self):
        handler = prepare_handler(WebAuthnStatusHandler(self.mock_app, self.mock_request))
        handler.get()
        assert handler.set_status.called
        assert handler.set_status.call_args[0][0] == 404

    def test_status_ok_when_enabled(self):
        constants.FEATURE_FLAGS["webauthn"] = True
        invalidate_feature_flags_cache()
        handler = prepare_handler(WebAuthnStatusHandler(self.mock_app, self.mock_request))
        handler.get()
        body = _response_body(handler)
        payload = json.loads(body)
        assert payload["enabled"] is True
        assert payload["rpId"] == "localhost"


class TestWebAuthnAuthOptionsHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "services": _default_services(),
        }
        self.mock_request.host = "localhost:8888"
        self.mock_request.protocol = "http"
        self.mock_request.body = b'{"username": "alice"}'

    def teardown_method(self):
        constants.FEATURE_FLAGS["webauthn"] = False
        invalidate_feature_flags_cache()

    def test_auth_options_404_when_disabled(self):
        handler = prepare_handler(WebAuthnAuthOptionsHandler(self.mock_app, self.mock_request))
        import asyncio

        asyncio.get_event_loop().run_until_complete(handler.post())
        assert handler.set_status.call_args[0][0] == 404

    def test_auth_options_no_credentials(self):
        constants.FEATURE_FLAGS["webauthn"] = True
        invalidate_feature_flags_cache()
        conn = _sqlite_conn()
        handler = prepare_handler(WebAuthnAuthOptionsHandler(self.mock_app, self.mock_request))
        with patch_db_conn(conn, modules=["aird.handlers.webauthn_handlers"]):
            import asyncio

            asyncio.get_event_loop().run_until_complete(handler.post())
        assert handler.set_status.call_args[0][0] == 400
        conn.close()

    def test_register_options_requires_auth(self):
        constants.FEATURE_FLAGS["webauthn"] = True
        invalidate_feature_flags_cache()
        conn = _sqlite_conn()
        handler = prepare_handler(
            WebAuthnRegisterOptionsHandler(self.mock_app, self.mock_request)
        )
        with patch_db_conn(conn, modules=["aird.handlers.webauthn_handlers"]):
            import asyncio
            import pytest
            from tornado.web import HTTPError

            with pytest.raises(HTTPError) as exc:
                asyncio.get_event_loop().run_until_complete(handler.post())
            assert exc.value.status_code == 403
        conn.close()

    def test_register_options_authenticated(self):
        constants.FEATURE_FLAGS["webauthn"] = True
        invalidate_feature_flags_cache()
        conn = _sqlite_conn()
        handler = prepare_handler(
            WebAuthnRegisterOptionsHandler(self.mock_app, self.mock_request)
        )
        authenticate(handler, role="user", username="alice")
        with patch_db_conn(conn, modules=["aird.handlers.webauthn_handlers"]):
            import asyncio

            asyncio.get_event_loop().run_until_complete(handler.post())
        body = _response_body(handler)
        payload = json.loads(body)
        assert "challenge" in payload
        assert payload.get("extensions", {}).get("prf") == {}
        conn.close()
