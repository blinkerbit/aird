"""Central SQLite schema for polls, lists, and meet."""

from __future__ import annotations

import sqlite3

from aird.plugins.collab.lists_db import LISTS_SCHEMA
from aird.plugins.collab.meet_db import MEET_SCHEMA
from aird.plugins.collab.polls_db import POLLS_SCHEMA
from aird.plugins.collab.refs import REFS_SCHEMA
from aird.plugins.collab.slugs import assign_slug

_SCHEMA = POLLS_SCHEMA + LISTS_SCHEMA + MEET_SCHEMA + REFS_SCHEMA


def collab_conn() -> sqlite3.Connection | None:
    import aird.constants as constants_module

    path = getattr(constants_module, "DB_PATH", None)
    if not path:
        return None
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    ensure_collab_schema(conn)
    return conn


def ensure_collab_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    for table in ("polls", "collab_lists", "meets"):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "slug" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN slug TEXT")
        if table == "meets" and "session_started_at" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN session_started_at TEXT")
        if table == "collab_lists" and "notes" not in cols:
            conn.execute("ALTER TABLE collab_lists ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        if table == "collab_lists" and "allow_forward" not in cols:
            conn.execute(
                "ALTER TABLE collab_lists ADD COLUMN allow_forward INTEGER NOT NULL DEFAULT 0"
            )
        if table == "collab_lists" and "read_only" not in cols:
            conn.execute(
                "ALTER TABLE collab_lists ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0"
            )
        title_col = "question" if table == "polls" else "title"
        missing = conn.execute(
            f"SELECT id, {title_col} FROM {table} WHERE slug IS NULL OR slug = ''"
        ).fetchall()
        for row in missing:
            assign_slug(conn, table, row[0], row[1] or "")
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_slug ON {table}(slug)"
        )
    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(list_items)")}
    for name, decl in (
        ("notes", "TEXT NOT NULL DEFAULT ''"),
        ("due_at", "TEXT"),
        ("remind_at", "TEXT"),
        ("reminded_at", "TEXT"),
    ):
        if name not in item_cols:
            conn.execute(f"ALTER TABLE list_items ADD COLUMN {name} {decl}")
    if "kind" not in item_cols:
        conn.execute("ALTER TABLE list_items ADD COLUMN kind TEXT NOT NULL DEFAULT 'checkbox'")
        conn.execute(
            "UPDATE list_items SET kind = 'bullet' "
            "WHERE list_id IN (SELECT id FROM collab_lists WHERE kind = 'bullet')"
        )
    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(list_items)")}
    for name, decl in (
        ("depth", "INTEGER NOT NULL DEFAULT 0"),
        ("order_key", "TEXT"),
        ("collapsed", "INTEGER NOT NULL DEFAULT 0"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
    ):
        if name not in item_cols:
            conn.execute(f"ALTER TABLE list_items ADD COLUMN {name} {decl}")
    # Normalize legacy kinds once columns exist.
    conn.execute("UPDATE list_items SET kind = 'checkbox' WHERE lower(kind) IN ('check', 'todo')")
    conn.execute("UPDATE list_items SET kind = 'paragraph' WHERE lower(kind) IN ('note', 'text')")
    missing_keys = conn.execute(
        "SELECT id, list_id, sort_order FROM list_items WHERE order_key IS NULL OR order_key = ''"
    ).fetchall()
    for row in missing_keys:
        key = f"{int(row['sort_order']):020d}"
        conn.execute("UPDATE list_items SET order_key = ? WHERE id = ?", (key, row["id"]))
    conn.commit()
