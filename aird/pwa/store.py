"""SQLite storage for Web Push subscriptions."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            endpoint TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user
        ON push_subscriptions (username)
        """
    )
    conn.commit()


def upsert_subscription(
    conn: sqlite3.Connection,
    *,
    username: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None = None,
) -> None:
    ensure_table(conn)
    now = _now()
    conn.execute(
        """
        INSERT INTO push_subscriptions (endpoint, username, p256dh, auth, user_agent, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            username = excluded.username,
            p256dh = excluded.p256dh,
            auth = excluded.auth,
            user_agent = excluded.user_agent,
            updated_at = excluded.updated_at
        """,
        (endpoint, username, p256dh, auth, user_agent or "", now, now),
    )
    conn.commit()


def delete_subscription(conn: sqlite3.Connection, *, endpoint: str, username: str | None = None) -> None:
    ensure_table(conn)
    if username:
        conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ? AND username = ?",
            (endpoint, username),
        )
    else:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()


def subscriptions_for_user(conn: sqlite3.Connection, username: str) -> list[dict]:
    ensure_table(conn)
    rows = conn.execute(
        "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE username = ?",
        (username,),
    ).fetchall()
    return [
        {
            "endpoint": r[0],
            "keys": {"p256dh": r[1], "auth": r[2]},
        }
        for r in rows
    ]


def delete_endpoint(conn: sqlite3.Connection, endpoint: str) -> None:
    ensure_table(conn)
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
