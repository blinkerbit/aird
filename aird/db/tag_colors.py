"""Per-tag display colors (ABAC tag name → hex)."""

from __future__ import annotations

import logging
import sqlite3

from aird.utils.tag_display import normalize_tag_color

logger = logging.getLogger(__name__)


def set_tag_color(conn: sqlite3.Connection | None, tag: str, color: str | None) -> bool:
    """Set or clear the display color for *tag*."""
    if conn is None or not tag:
        return False
    if color is not None and str(color).strip() and normalize_tag_color(color) is None:
        return False
    norm = normalize_tag_color(color)
    try:
        with conn:
            if norm is None:
                conn.execute("DELETE FROM tag_colors WHERE tag = ?", (tag,))
                return True
            conn.execute(
                "INSERT INTO tag_colors (tag, color) VALUES (?, ?) "
                "ON CONFLICT(tag) DO UPDATE SET color = excluded.color",
                (tag, norm),
            )
            return True
    except sqlite3.Error as exc:
        logger.warning("set_tag_color failed for %r: %s", tag, exc)
        return False


def delete_tag_color(conn: sqlite3.Connection | None, tag: str) -> bool:
    if conn is None or not tag:
        return False
    try:
        with conn:
            conn.execute("DELETE FROM tag_colors WHERE tag = ?", (tag,))
            return True
    except sqlite3.Error:
        return False


def get_tag_colors_map(conn: sqlite3.Connection | None) -> dict[str, str]:
    """Map tag name → #rrggbb color."""
    if conn is None:
        return {}
    try:
        rows = conn.execute("SELECT tag, color FROM tag_colors").fetchall()
        out: dict[str, str] = {}
        for tag, color in rows:
            norm = normalize_tag_color(color)
            if tag and norm:
                out[str(tag)] = norm
        return out
    except sqlite3.Error:
        return {}
