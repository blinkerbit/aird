"""Tests for tag rename across rules and colors."""

import sqlite3

import pytest

from aird.db.resource_tags import insert_resource_tag, list_resource_tags, rename_resource_tag_name
from aird.db.tag_colors import get_tag_colors_map, rename_tag_color, set_tag_color


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE resource_tags ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL, glob_pattern TEXT NOT NULL, "
        "priority INTEGER NOT NULL DEFAULT 0, created_at TEXT, created_by TEXT, "
        "UNIQUE(tag, glob_pattern))"
    )
    conn.execute(
        "CREATE TABLE tag_colors (tag TEXT PRIMARY KEY, color TEXT NOT NULL)"
    )
    conn.commit()
    yield conn
    conn.close()


class TestRenameResourceTag:
    def test_renames_all_rules(self, db_conn):
        insert_resource_tag(db_conn, "old", "/a/**")
        insert_resource_tag(db_conn, "old", "/b/**")
        set_tag_color(db_conn, "old", "#ff0000")
        count = rename_resource_tag_name(db_conn, "old", "new")
        assert count == 2
        tags = {r["tag"] for r in list_resource_tags(db_conn)}
        assert tags == {"new"}
        rename_tag_color(db_conn, "old", "new")
        assert get_tag_colors_map(db_conn) == {"new": "#ff0000"}

    def test_noop_when_same_name(self, db_conn):
        insert_resource_tag(db_conn, "x", "/x")
        assert rename_resource_tag_name(db_conn, "x", "x") == 0

    def test_missing_tag_returns_zero(self, db_conn):
        assert rename_resource_tag_name(db_conn, "nope", "other") == 0
