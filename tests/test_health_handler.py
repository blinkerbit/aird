"""Tests for /health and transfer service worker routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aird.handlers.health_handler import HealthHandler, ServiceWorkerHandler
from tests.handler_helpers import prepare_handler


@pytest.fixture
def mock_app():
    app = MagicMock()
    app.settings = {"cookie_secret": "test"}
    return app


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.protocol = "http"
    return req


class TestHealthHandler:
    def test_healthy_when_db_probe_succeeds(self, mock_app, mock_request):
        handler = prepare_handler(HealthHandler(mock_app, mock_request))
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = (1,)
        with patch("aird.handlers.health_handler.constants_module.DB_CONN", db):
            handler.get()
        handler.set_status.assert_not_called()
        payload = handler.write.call_args[0][0]
        assert payload["status"] == "ok"
        assert payload["db"] == "ok"

    def test_unhealthy_when_db_probe_fails(self, mock_app, mock_request):
        handler = prepare_handler(HealthHandler(mock_app, mock_request))
        db = MagicMock()
        db.execute.side_effect = RuntimeError("db down")
        with patch("aird.handlers.health_handler.constants_module.DB_CONN", db):
            handler.get()
        handler.set_status.assert_called_once_with(503)
        payload = handler.write.call_args[0][0]
        assert payload["status"] == "error"
        assert payload["db"] == "error"

    def test_db_not_configured_is_still_ok(self, mock_app, mock_request):
        handler = prepare_handler(HealthHandler(mock_app, mock_request))
        with patch("aird.handlers.health_handler.constants_module.DB_CONN", None):
            handler.get()
        handler.set_status.assert_not_called()
        payload = handler.write.call_args[0][0]
        assert payload["db"] == "not_configured"


class TestServiceWorkerHandler:
    def test_serves_sw_transfer_js(self, mock_app, mock_request):
        handler = prepare_handler(ServiceWorkerHandler(mock_app, mock_request))
        handler.get()
        handler.set_header.assert_any_call(
            "Content-Type", "application/javascript; charset=utf-8"
        )
        handler.set_header.assert_any_call("Service-Worker-Allowed", "/")
        assert handler.write.called
        body = handler.write.call_args[0][0]
        assert b"sw-transfer" in body or len(body) > 0
