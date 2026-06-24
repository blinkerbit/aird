"""Tests for ABAC admin handlers and helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from aird.db import init_db
from aird.handlers.abac_handlers import (
    AdminPolicyAPIHandler,
    AdminTagAPIHandler,
    AdminUserAttributeAPIHandler,
    PolicyDecisionsAPIHandler,
    _bool_arg,
    _parse_actions,
    _parse_condition,
    _parse_target_actions,
    _validate_policy_payload,
)
from tests.handler_helpers import _default_services, authenticate, patch_db_conn, prepare_handler


@pytest.fixture
def db_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def admin_app():
    app = MagicMock()
    app.settings = {
        "cookie_secret": "test",
        "services": _default_services(),
    }
    return app


@pytest.fixture
def admin_request():
    req = MagicMock()
    req.body = b"{}"
    req.remote_ip = "127.0.0.1"
    req.connection = MagicMock()
    req.connection.context = MagicMock()
    req.protocol = "http"
    return req


class TestAbacHelpers:
    def test_bool_arg(self):
        assert _bool_arg(None) is False
        assert _bool_arg("true") is True
        assert _bool_arg("0") is False

    def test_parse_actions(self):
        assert _parse_actions("") == []
        assert _parse_actions("read, write") == ["read", "write"]
        assert _parse_actions('["read","write"]') == ["read", "write"]
        assert _parse_actions("[bad") == ["[bad"]

    def test_parse_target_actions(self):
        assert _parse_target_actions("a,b") == ["a", "b"]
        assert _parse_target_actions(["x"]) == ["x"]
        assert _parse_target_actions(42) == []

    def test_parse_condition(self):
        assert _parse_condition(None) == ({}, None)
        assert _parse_condition('{"role":"admin"}') == ({"role": "admin"}, None)
        cond, err = _parse_condition("{bad")
        assert cond == {}
        assert err is not None
        cond, err = _parse_condition([])
        assert err == "condition must be a JSON object"

    def test_validate_policy_payload(self):
        data, err = _validate_policy_payload({})
        assert err == "name is required"
        data, err = _validate_policy_payload(
            {
                "name": "p1",
                "effect": "permit",
                "target_actions": ["read"],
                "condition": {},
                "enabled": "on",
            }
        )
        assert err is None
        assert data["enabled"] is True


class TestAdminTagAPIHandler:
    def test_tag_crud(self, admin_app, admin_request, db_conn):
        handler = AdminTagAPIHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn):
            handler.get()
            assert "tags" in handler.write.call_args[0][0]

            admin_request.body = json.dumps(
                {"tag": "conf", "glob_pattern": "*.secret", "priority": 1}
            ).encode()
            handler.post()
            handler.set_status.assert_called_with(201)
            tag_id = handler.write.call_args[0][0]["id"]

            admin_request.body = json.dumps(
                {"id": tag_id, "glob_pattern": "*.classified"}
            ).encode()
            handler.put()
            assert handler.write.call_args[0][0]["updated"] is True

            admin_request.body = json.dumps({"id": tag_id}).encode()
            handler.delete()
            assert handler.write.call_args[0][0]["deleted"] is True

    def test_tag_post_validation(self, admin_app, admin_request, db_conn):
        handler = AdminTagAPIHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn):
            admin_request.body = json.dumps({"tag": "", "glob_pattern": ""}).encode()
            handler.post()
            handler.set_status.assert_called_with(400)

            admin_request.body = json.dumps({"ids": []}).encode()
            handler.delete()
            handler.set_status.assert_called_with(400)


class TestAdminPolicyAPIHandler:
    def test_policy_crud(self, admin_app, admin_request, db_conn):
        handler = AdminPolicyAPIHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn):
            handler.get()
            assert "policies" in handler.write.call_args[0][0]

            admin_request.body = json.dumps(
                {
                    "name": "allow-read",
                    "effect": "permit",
                    "target_actions": ["read"],
                    "condition": {"role": "user"},
                }
            ).encode()
            handler.post()
            handler.set_status.assert_called_with(201)
            pid = handler.write.call_args[0][0]["id"]

            handler.get(str(pid))
            assert handler.write.call_args[0][0]["name"] == "allow-read"

            admin_request.body = json.dumps(
                {
                    "name": "allow-read",
                    "effect": "permit",
                    "target_actions": ["read", "write"],
                    "condition": {},
                }
            ).encode()
            handler.post(str(pid))
            assert handler.write.call_args[0][0]["updated"] is True

            admin_request.body = b"{}"
            handler.delete(str(pid))
            assert handler.write.call_args[0][0]["deleted"] is True


class TestAdminUserAttributeAPIHandler:
    def test_user_attribute_api(self, admin_app, admin_request, db_conn):
        handler = AdminUserAttributeAPIHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn):
            admin_request.body = json.dumps(
                {"username": "alice", "key": "dept", "value": "eng"}
            ).encode()
            handler.post()
            assert handler.write.call_args[0][0]["ok"] is True

            handler.get()
            assert len(handler.write.call_args[0][0]["attrs"]) == 1

            admin_request.body = json.dumps(
                {"username": "alice", "key": "dept"}
            ).encode()
            handler.delete()
            assert handler.write.call_args[0][0]["deleted"] is True


class TestPolicyDecisionsAPIHandler:
    def test_list_decisions(self, admin_app, admin_request, db_conn):
        from aird.db.policy_decisions import log_policy_decision

        log_policy_decision(
            db_conn, username="alice", action="read", decision="permit"
        )
        handler = PolicyDecisionsAPIHandler(admin_app, admin_request)
        authenticate(handler)
        handler.get_argument = MagicMock(side_effect=lambda k, default="": {"limit": "10", "offset": "0"}.get(k, default))
        with patch_db_conn(db_conn):
            handler.get()
            payload = handler.write.call_args[0][0]
            assert len(payload["decisions"]) == 1


class TestAdminTagsPageHandler:
    def test_render_tags_page(self, admin_app, admin_request, db_conn):
        from aird.handlers.abac_handlers import AdminTagsHandler

        handler = AdminTagsHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn), patch.object(handler, "render") as render:
            handler.get()
            render.assert_called_once()
            assert render.call_args[0][0] == "admin_tags.html"


class TestAbacPolicyValidation:
    def test_policy_post_validation_error(self, admin_app, admin_request, db_conn):
        handler = AdminPolicyAPIHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn):
            admin_request.body = json.dumps({"name": ""}).encode()
            handler.post()
            handler.set_status.assert_called_with(400)

    def test_policy_get_not_found(self, admin_app, admin_request, db_conn):
        handler = AdminPolicyAPIHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn):
            handler.get("99999")
            handler.set_status.assert_called_with(404)


class TestAbacTagDeletePaths:
    def test_delete_by_name_and_bulk(self, admin_app, admin_request, db_conn):
        from aird.db.resource_tags import insert_resource_tag

        tid = insert_resource_tag(db_conn, "t1", "*.a")
        handler = AdminTagAPIHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn):
            admin_request.body = json.dumps({"tag": "t1"}).encode()
            handler.delete()
            assert handler.write.call_args[0][0]["count"] == 1

            tid2 = insert_resource_tag(db_conn, "t2", "*.b")
            admin_request.body = json.dumps({"ids": [tid2]}).encode()
            handler.delete()
            assert tid2 in handler.write.call_args[0][0]["ids"]

            admin_request.body = json.dumps({"id": 99999}).encode()
            handler.delete()
            handler.set_status.assert_called_with(404)


class TestPolicyDecisionsWebSocket:
    def test_open_non_admin_closed(self):
        from aird.handlers.abac_handlers import PolicyDecisionsWebSocket

        app = MagicMock()
        app.settings = {"services": _default_services()}
        handler = PolicyDecisionsWebSocket(app, MagicMock())
        handler.get_current_user = MagicMock(return_value={"username": "user", "role": "user"})
        handler.register_connection = MagicMock(return_value=True)
        handler.close = MagicMock()
        handler.open()
        handler.close.assert_called_once()

    def test_on_decision_broadcast(self):
        from aird.core.events import PolicyDecisionEvent, now_ts
        from aird.handlers.abac_handlers import PolicyDecisionsWebSocket

        handler = PolicyDecisionsWebSocket(MagicMock(), MagicMock())
        handler.write_message = MagicMock()
        event = PolicyDecisionEvent(
            "alice", "read", "/a", "permit", "ok", 1, "policy", "127.0.0.1", now_ts()
        )
        handler._on_decision(event)
        handler.write_message.assert_called_once()


class TestAdminHtmlHandlers:
    def test_admin_policies_page(self, admin_app, admin_request, db_conn):
        from aird.handlers.abac_handlers import AdminPoliciesHandler, AdminUserAttributesHandler

        handler = AdminPoliciesHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn), patch.object(handler, "render") as render:
            handler.get()
            render.assert_called_once()
            assert render.call_args[0][0] == "admin_policies.html"
            assert isinstance(render.call_args[1]["policies"], list)

        attrs_handler = AdminUserAttributesHandler(admin_app, admin_request)
        authenticate(attrs_handler)
        with patch_db_conn(db_conn), patch.object(attrs_handler, "render") as render:
            attrs_handler.get()
            render.assert_called_once()


class TestAbacTagPutErrors:
    def test_put_not_found(self, admin_app, admin_request, db_conn):
        handler = AdminTagAPIHandler(admin_app, admin_request)
        authenticate(handler)
        with patch_db_conn(db_conn):
            admin_request.body = json.dumps(
                {"id": 99999, "glob_pattern": "*.x"}
            ).encode()
            handler.put()
            handler.set_status.assert_called_with(404)
