"""Tests for tag color storage and display."""

import sqlite3

import pytest

from aird.db.tag_colors import get_tag_colors_map, set_tag_color, delete_tag_color
from aird.utils.tag_display import normalize_tag_color, tag_chip_inline_style


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE tag_colors (tag TEXT PRIMARY KEY, color TEXT NOT NULL)"
    )
    conn.commit()
    yield conn
    conn.close()


class TestTagDisplay:
    def test_normalize_hex6(self):
        assert normalize_tag_color("#AABBCC") == "#aabbcc"

    def test_normalize_hex3(self):
        assert normalize_tag_color("#abc") == "#aabbcc"

    def test_normalize_invalid(self):
        assert normalize_tag_color("red") is None
        assert normalize_tag_color("") is None

    def test_chip_style_empty_when_no_color(self):
        assert tag_chip_inline_style(None) == ""

    def test_chip_style_with_color(self):
        style = tag_chip_inline_style("#ff0000")
        assert "background:#ff0000" in style
        assert "color:" in style


class TestTagColorsDb:
    def test_set_and_get(self, db_conn):
        assert set_tag_color(db_conn, "pii", "#ff0000")
        assert get_tag_colors_map(db_conn) == {"pii": "#ff0000"}

    def test_update_color(self, db_conn):
        set_tag_color(db_conn, "pii", "#ff0000")
        set_tag_color(db_conn, "pii", "#00ff00")
        assert get_tag_colors_map(db_conn)["pii"] == "#00ff00"

    def test_clear_color(self, db_conn):
        set_tag_color(db_conn, "pii", "#ff0000")
        set_tag_color(db_conn, "pii", None)
        assert get_tag_colors_map(db_conn) == {}

    def test_delete_tag_color(self, db_conn):
        set_tag_color(db_conn, "docs", "#6366f1")
        delete_tag_color(db_conn, "docs")
        assert get_tag_colors_map(db_conn) == {}

    def test_invalid_color_rejected(self, db_conn):
        assert not set_tag_color(db_conn, "pii", "not-a-color")
        assert get_tag_colors_map(db_conn) == {}
