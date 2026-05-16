"""Tests for TagService glob-based tag resolution."""

import sqlite3

import pytest

from aird.db import init_db
from aird.services.tag_service import TagService


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_db(c)
    yield c
    c.close()


def test_apply_creates_rule_and_returns_id(conn):
    svc = TagService(cache_ttl=0)
    rule_id = svc.apply(conn, "pii", "finance/*.pdf", priority=10)
    assert rule_id is not None
    assert any(r["tag"] == "pii" for r in svc.list(conn))


def test_resolve_matches_glob_pattern(conn):
    svc = TagService(cache_ttl=0)
    svc.apply(conn, "pii", "finance/*.pdf", priority=10)
    svc.apply(conn, "logs", "*.log", priority=5)

    assert svc.resolve(conn, "finance/q3.pdf") == ("pii",)
    assert svc.resolve(conn, "app.log") == ("logs",)
    assert svc.resolve(conn, "image.png") == ()


def test_resolve_normalises_windows_separators(conn):
    svc = TagService(cache_ttl=0)
    svc.apply(conn, "pii", "finance/*.pdf")
    assert svc.resolve(conn, "finance\\q3.pdf") == ("pii",)


def test_resolve_priority_orders_tags(conn):
    svc = TagService(cache_ttl=0)
    svc.apply(conn, "low", "*.pdf", priority=1)
    svc.apply(conn, "high", "finance/*.pdf", priority=99)

    tags = svc.resolve(conn, "finance/q3.pdf")
    assert tags[0] == "high"
    assert "low" in tags


def test_remove_invalidates_cache(conn):
    svc = TagService(cache_ttl=60)
    rule_id = svc.apply(conn, "pii", "finance/*.pdf")
    assert svc.resolve(conn, "finance/x.pdf") == ("pii",)
    assert svc.remove(conn, rule_id) is True
    assert svc.resolve(conn, "finance/x.pdf") == ()


def test_resolve_returns_empty_for_missing_path(conn):
    svc = TagService(cache_ttl=0)
    svc.apply(conn, "pii", "*")
    assert svc.resolve(conn, None) == ()
    assert svc.resolve(conn, "") == ()


def test_apply_duplicate_tag_glob_returns_none(conn):
    svc = TagService(cache_ttl=0)
    assert svc.apply(conn, "pii", "*.pdf") is not None
    assert svc.apply(conn, "pii", "*.pdf") is None
