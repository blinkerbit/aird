"""Server-side login session storage and validation."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from aird.constants import (
    SESSION_IDLE_TIMEOUT_SECONDS,
    SESSION_MAX_AGE_SECONDS,
    SESSION_TOUCH_INTERVAL_SECONDS,
)

SESSION_COOKIE_NAME = "aird_session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def create_session(
    conn: sqlite3.Connection,
    *,
    username: str,
    user_role: str,
    is_admin: bool = False,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a persisted session row and return its opaque id."""
    session_id = secrets.token_urlsafe(32)
    now = _now()
    conn.execute(
        """
        INSERT INTO user_sessions
            (id, username, user_role, is_admin, created_at, last_active_at,
             expires_at, ip_address, user_agent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            username,
            user_role,
            1 if is_admin else 0,
            _iso(now),
            _iso(now),
            _iso(now + timedelta(seconds=SESSION_MAX_AGE_SECONDS)),
            (ip_address or "")[:64] or None,
            (user_agent or "")[:512] or None,
        ),
    )
    conn.commit()
    return session_id


def _row_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "username": row[1],
        "user_role": row[2],
        "is_admin": bool(row[3]),
        "created_at": row[4],
        "last_active_at": row[5],
        "expires_at": row[6],
        "ip_address": row[7],
        "user_agent": row[8],
    }


def _is_expired(session: dict[str, Any], now: datetime | None = None) -> bool:
    current = now or _now()
    expires_at = _parse_iso(session.get("expires_at"))
    if expires_at is not None and current > expires_at:
        return True
    last_active = _parse_iso(session.get("last_active_at"))
    if last_active is None:
        return True
    idle_seconds = (current - last_active).total_seconds()
    return idle_seconds > SESSION_IDLE_TIMEOUT_SECONDS


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    """Return a session row if it exists and is still valid."""
    if not session_id:
        return None
    row = conn.execute(
        """
        SELECT id, username, user_role, is_admin, created_at, last_active_at,
               expires_at, ip_address, user_agent
        FROM user_sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    session = _row_to_dict(row)
    if _is_expired(session):
        revoke_session_by_id(conn, session_id)
        return None
    return session


def touch_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Refresh idle timer (throttled to reduce writes)."""
    row = conn.execute(
        "SELECT last_active_at FROM user_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return
    last_active = _parse_iso(row[0])
    now = _now()
    if (
        last_active is not None
        and (now - last_active).total_seconds() < SESSION_TOUCH_INTERVAL_SECONDS
    ):
        return
    conn.execute(
        "UPDATE user_sessions SET last_active_at = ? WHERE id = ?",
        (_iso(now), session_id),
    )
    conn.commit()


def revoke_session_by_id(conn: sqlite3.Connection, session_id: str) -> bool:
    cursor = conn.execute("DELETE FROM user_sessions WHERE id = ?", (session_id,))
    conn.commit()
    return cursor.rowcount > 0


def revoke_session_for_user(
    conn: sqlite3.Connection, session_id: str, username: str
) -> bool:
    cursor = conn.execute(
        "DELETE FROM user_sessions WHERE id = ? AND username = ?",
        (session_id, username),
    )
    conn.commit()
    return cursor.rowcount > 0


def revoke_other_sessions(
    conn: sqlite3.Connection, username: str, keep_session_id: str
) -> int:
    cursor = conn.execute(
        "DELETE FROM user_sessions WHERE username = ? AND id != ?",
        (username, keep_session_id),
    )
    conn.commit()
    return cursor.rowcount


def list_sessions_for_user(
    conn: sqlite3.Connection, username: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, username, user_role, is_admin, created_at, last_active_at,
               expires_at, ip_address, user_agent
        FROM user_sessions
        WHERE username = ?
        ORDER BY last_active_at DESC
        """,
        (username,),
    ).fetchall()
    active: list[dict[str, Any]] = []
    for row in rows:
        session = _row_to_dict(row)
        if _is_expired(session):
            revoke_session_by_id(conn, session["id"])
            continue
        active.append(session)
    return active


def cleanup_expired_sessions(conn: sqlite3.Connection) -> int:
    """Remove sessions past absolute expiry or idle timeout."""
    rows = conn.execute(
        """
        SELECT id, created_at, last_active_at, expires_at
        FROM user_sessions
        """
    ).fetchall()
    removed = 0
    now = _now()
    for row in rows:
        session = {
            "id": row[0],
            "created_at": row[1],
            "last_active_at": row[2],
            "expires_at": row[3],
        }
        if _is_expired(session, now) and revoke_session_by_id(conn, row[0]):
            removed += 1
    return removed
