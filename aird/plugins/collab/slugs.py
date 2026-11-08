"""Short names such as grocery-nov7 for mentions."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

_TABLES = {"polls", "collab_lists", "meets"}


def slugify(title: str, when: datetime | None = None) -> str:
    moment = when or datetime.now(timezone.utc)
    base = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:28] or "item"
    return f"{base}-{moment.strftime('%b').lower()}{moment.day}"


def clean_slug(raw: str | None, fallback_title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(raw or "").lower()).strip("-")[:40]
    return text or slugify(fallback_title)


def unique_slug(conn: sqlite3.Connection, table: str, title: str, preferred: str | None = None, *, except_id: str | None = None) -> str:
    if table not in _TABLES:
        raise ValueError("unknown table")
    seed = clean_slug(preferred, title)
    slug = seed
    n = 2
    while True:
        row = conn.execute(f"SELECT id FROM {table} WHERE slug = ?", (slug,)).fetchone()
        if not row or (except_id and str(row[0]) == except_id):
            return slug
        slug = f"{seed}-{n}"
        n += 1


def assign_slug(conn: sqlite3.Connection, table: str, artifact_id: str, title: str, preferred: str | None = None) -> str:
    slug = unique_slug(conn, table, title, preferred, except_id=artifact_id)
    conn.execute(f"UPDATE {table} SET slug = ? WHERE id = ?", (slug, artifact_id))
    return slug
