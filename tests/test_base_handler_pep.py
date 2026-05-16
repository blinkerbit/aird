"""Tests for the ABAC PEP wiring in BaseHandler."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from aird.db import init_db
from aird.db.policies import insert_policy
from aird.handlers.base_handler import BaseHandler, require_action, require_admin
from aird.services.policy_service import PolicyService
from aird.services.tag_service import TagService
from tests.handler_helpers import _default_services


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_db(c)
    yield c
    c.close()


def _make_handler(conn, services, *, role="user"):
    app = MagicMock()
    app.settings = {
        "cookie_secret": "test",
        "services": services,
        "db_conn": conn,
    }
    request = MagicMock()
    request.headers = {}
    request.remote_ip = "127.0.0.1"
    handler = BaseHandler(app, request)
    handler._transforms = []
    handler.write = MagicMock()
    handler.set_status = MagicMock()
    handler.redirect = MagicMock()
    handler.get_current_user = MagicMock(
        return_value={"username": "alice", "role": role}
    )
    handler._current_user = {"username": "alice", "role": role}
    return handler


def _build_services(conn):
    services = _default_services()
    tag_service = TagService(cache_ttl=0)
    policy_service = PolicyService(tag_service, cache_ttl=0)
    services["tag_service"] = tag_service
    services["policy_service"] = policy_service
    return services


def test_check_access_returns_none_when_flag_disabled(conn):
    services = _build_services(conn)
    handler = _make_handler(conn, services)
    with patch(
        "aird.handlers.base_handler.is_feature_enabled", return_value=False
    ):
        decision = handler.check_access("file.read", "/data/x.txt")
    assert decision is None


def test_check_access_evaluates_when_flag_enabled(conn):
    services = _build_services(conn)
    insert_policy(
        conn,
        name="permit-read",
        effect="permit",
        target_actions=["file.read"],
        condition={},
        priority=10,
    )
    handler = _make_handler(conn, services)
    with patch(
        "aird.handlers.base_handler.is_feature_enabled", return_value=True
    ):
        decision = handler.check_access("file.read", "/data/x.txt")
    assert decision is not None
    assert decision.is_permit


def test_require_action_blocks_on_deny(conn):
    services = _build_services(conn)
    # Disable the seeded user-permit so the request hits the implicit default deny.
    conn.execute("UPDATE policies SET enabled = 0")
    conn.commit()
    handler = _make_handler(conn, services)

    @require_action("file.read")
    def view(self):
        return "ok"

    with patch(
        "aird.handlers.base_handler.is_feature_enabled", return_value=True
    ):
        result = view(handler)
    assert result is None
    handler.set_status.assert_called_with(403)


def test_require_action_passthrough_when_flag_off(conn):
    services = _build_services(conn)
    handler = _make_handler(conn, services)

    @require_action("file.read")
    def view(self):
        return "ok"

    with patch(
        "aird.handlers.base_handler.is_feature_enabled", return_value=False
    ):
        result = view(handler)
    assert result == "ok"


def test_require_admin_keeps_legacy_behaviour_when_flag_off(conn):
    services = _build_services(conn)
    handler = _make_handler(conn, services, role="user")
    handler.is_admin_user = MagicMock(return_value=False)

    @require_admin(deny_status=403, deny_body="nope")
    def view(self):
        return "ok"

    with patch(
        "aird.handlers.base_handler.is_feature_enabled", return_value=False
    ):
        result = view(handler)
    assert result is None
    handler.set_status.assert_called_with(403)


def test_require_admin_consults_pdp_when_flag_on(conn):
    services = _build_services(conn)
    insert_policy(
        conn,
        name="default-admin-permit",
        effect="permit",
        target_actions=["*"],
        condition={"equals": {"left": {"attr": "subject.role"}, "right": "admin"}},
        priority=1000,
    )
    handler = _make_handler(conn, services, role="admin")
    handler.is_admin_user = MagicMock(return_value=True)

    @require_admin(deny_status=403, deny_body="nope")
    def view(self):
        return "ok"

    with patch(
        "aird.handlers.base_handler.is_feature_enabled", return_value=True
    ):
        result = view(handler)
    assert result == "ok"
