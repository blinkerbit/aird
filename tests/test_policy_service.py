"""Tests for the ABAC PolicyService PDP."""

import sqlite3
from datetime import datetime

import pytest

from aird.core.events import EventBus, PolicyDecisionEvent
from aird.db import init_db
from aird.db.policies import insert_policy, list_policies
from aird.domain.models import (
    AccessRequest,
    EnvironmentContext,
    ResourceAttributes,
    SubjectAttributes,
)
from aird.services.policy_service import (
    PolicyService,
    _evaluate_condition,
    build_request,
)
from aird.services.tag_service import TagService


def _make_request(
    *,
    role="user",
    action="file.read",
    resource_path=None,
    timestamp=None,
    is_managed_device=False,
    ip=None,
):
    subject = SubjectAttributes(username="alice", role=role, clearance=role)
    resource = ResourceAttributes(path=resource_path)
    env = EnvironmentContext(
        timestamp=timestamp or datetime(2025, 5, 8, 12, 0, 0),
        ip=ip,
        is_managed_device=is_managed_device,
    )
    return AccessRequest(subject=subject, action=action, resource=resource, environment=env)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_db(c)
    # Wipe seeded policies so tests can construct exactly the rules they need.
    c.execute("DELETE FROM policies")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def services():
    tag_service = TagService(cache_ttl=0)
    bus = EventBus()
    return PolicyService(tag_service, event_bus=bus, cache_ttl=0), tag_service, bus


def test_default_deny_when_no_policies(conn, services):
    policy_service, _, _ = services
    decision = policy_service.evaluate(conn, _make_request(), audit=False)
    assert decision.is_deny


def test_admin_permit_short_circuits(conn, services):
    policy_service, _, _ = services
    insert_policy(
        conn,
        name="admin-permit",
        effect="permit",
        target_actions=["*"],
        condition={"equals": {"left": {"attr": "subject.role"}, "right": "admin"}},
        priority=1000,
    )
    decision = policy_service.evaluate(conn, _make_request(role="admin"), audit=False)
    assert decision.is_permit
    assert decision.matched_policy_name == "admin-permit"


def test_explicit_deny_wins_over_lower_priority_permit(conn, services):
    policy_service, _, _ = services
    insert_policy(
        conn,
        name="permit-everyone",
        effect="permit",
        target_actions=["file.read"],
        condition={},
        priority=10,
    )
    insert_policy(
        conn,
        name="deny-pii",
        effect="deny",
        target_actions=["file.read"],
        condition={"tag_present": "pii"},
        priority=100,
    )
    request = _make_request(resource_path="finance/q3.pdf")
    # Inject the tag manually instead of going through tag service.
    object.__setattr__(request.resource, "tags", ("pii",))
    decision = policy_service.evaluate(conn, request, audit=False)
    assert decision.is_deny
    assert decision.matched_policy_name == "deny-pii"


def test_priority_orders_permits(conn, services):
    policy_service, _, _ = services
    insert_policy(
        conn,
        name="low",
        effect="permit",
        target_actions=["file.read"],
        condition={},
        priority=1,
        description="low",
    )
    insert_policy(
        conn,
        name="high",
        effect="permit",
        target_actions=["file.read"],
        condition={},
        priority=100,
        description="high",
    )
    decision = policy_service.evaluate(conn, _make_request(), audit=False)
    assert decision.is_permit
    assert decision.matched_policy_name == "high"


def test_action_wildcard(conn, services):
    policy_service, _, _ = services
    insert_policy(
        conn,
        name="wildcard",
        effect="permit",
        target_actions=["*"],
        condition={},
        priority=10,
    )
    decision = policy_service.evaluate(
        conn, _make_request(action="anything.goes"), audit=False
    )
    assert decision.is_permit


def test_disabled_policy_is_skipped(conn, services):
    policy_service, _, _ = services
    insert_policy(
        conn,
        name="disabled-permit",
        effect="permit",
        target_actions=["file.read"],
        condition={},
        priority=10,
        enabled=False,
    )
    decision = policy_service.evaluate(conn, _make_request(), audit=False)
    assert decision.is_deny


def test_emits_event_on_decision(conn, services):
    policy_service, _, bus = services
    insert_policy(
        conn,
        name="permit",
        effect="permit",
        target_actions=["file.read"],
        condition={},
        priority=10,
    )
    captured = []
    bus.subscribe(PolicyDecisionEvent, captured.append)
    policy_service.evaluate(conn, _make_request(), audit=False)
    assert len(captured) == 1
    assert captured[0].decision == "permit"


def test_persists_audit_row_when_enabled(conn, services):
    policy_service, _, _ = services
    insert_policy(
        conn,
        name="permit",
        effect="permit",
        target_actions=["file.read"],
        condition={},
        priority=10,
    )
    policy_service.evaluate(conn, _make_request(), audit=True)
    rows = conn.execute(
        "SELECT decision, action FROM policy_decisions"
    ).fetchall()
    assert rows == [("permit", "file.read")]


def test_tag_service_resolves_resource_tags(conn, services):
    policy_service, tag_service, _ = services
    tag_service.apply(conn, "pii", "finance/*.pdf", priority=10)
    insert_policy(
        conn,
        name="deny-pii",
        effect="deny",
        target_actions=["file.read"],
        condition={"tag_present": "pii"},
        priority=10,
    )
    decision = policy_service.evaluate(
        conn, _make_request(resource_path="finance/q3.pdf"), audit=False
    )
    assert decision.is_deny


def test_seeded_admin_permit_runs_after_init(tmp_path):
    # init_db seeds policies; ensure the default-admin-permit row is present.
    c = sqlite3.connect(":memory:")
    init_db(c)
    names = {p["name"] for p in list_policies(c)}
    assert "default-admin-permit" in names
    assert "default-user-permit" in names
    assert "time-gated-pii" in names


# ---------------------------------------------------------------------------
# AST evaluator
# ---------------------------------------------------------------------------


def test_ast_and_or_not():
    attrs = {"subject": {"role": "user"}}
    assert _evaluate_condition({"and": [True, True]}, attrs)
    assert not _evaluate_condition({"and": [True, False]}, attrs)
    assert _evaluate_condition({"or": [False, True]}, attrs)
    assert not _evaluate_condition({"not": True}, attrs)


def test_ast_equals_with_attr_lookup():
    attrs = {"subject": {"role": "admin"}}
    expr = {"equals": {"left": {"attr": "subject.role"}, "right": "admin"}}
    assert _evaluate_condition(expr, attrs)


def test_ast_in_membership():
    attrs = {"subject": {"role": "user"}}
    expr = {"in": {"value": {"attr": "subject.role"}, "list": ["admin", "user"]}}
    assert _evaluate_condition(expr, attrs)


def test_ast_time_between_business_hours():
    attrs = {"environment": {"timestamp": "2025-05-08T11:30:00"}}
    assert _evaluate_condition(
        {"time_between": {"start": "09:00", "end": "18:00"}}, attrs
    )
    attrs_late = {"environment": {"timestamp": "2025-05-08T22:30:00"}}
    assert not _evaluate_condition(
        {"time_between": {"start": "09:00", "end": "18:00"}}, attrs_late
    )


def test_ast_tag_present():
    attrs = {"resource": {"tags": ["pii", "finance"]}}
    assert _evaluate_condition({"tag_present": "pii"}, attrs)
    assert not _evaluate_condition({"tag_present": "secret"}, attrs)


def test_ast_ip_in_cidr():
    attrs = {"environment": {"ip": "10.0.0.5"}}
    assert _evaluate_condition({"ip_in_cidr": {"cidr": "10.0.0.0/8"}}, attrs)
    assert not _evaluate_condition({"ip_in_cidr": {"cidr": "192.168.0.0/16"}}, attrs)


def test_build_request_extracts_extension():
    req = build_request(username="alice", action="file.read", resource_path="/x/y/file.pdf")
    assert req.resource.extension == "pdf"
