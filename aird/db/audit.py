"""Audit log database operations."""

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def log_audit(
    conn: sqlite3.Connection,
    action: str,
    username: str = None,
    details: str = None,
    ip: str = None,
) -> None:
    """Append an audit log entry. Safe to call with conn=None (no-op)."""
    if conn is None:
        return
    try:
        created = datetime.now(timezone.utc).isoformat() + "Z"
        with conn:
            conn.execute(
                "INSERT INTO audit_log (created_at, username, action, details, ip) VALUES (?, ?, ?, ?, ?)",
                (created, username, action, details, ip),
            )
    except Exception as e:
        logging.warning("Audit log write failed: %s", e)


def get_audit_logs(conn: sqlite3.Connection, limit: int = 500, offset: int = 0) -> list:
    """Return recent audit log entries, newest first."""
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, created_at, username, action, details, ip FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1],
                "username": r[2],
                "action": r[3],
                "details": r[4],
                "ip": r[5],
            }
            for r in rows
        ]
    except Exception:
        return []
