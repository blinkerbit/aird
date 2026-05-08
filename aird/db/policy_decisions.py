"""Policy decision audit log database operations."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def log_policy_decision(
    conn: sqlite3.Connection,
    *,
    username: str | None,
    action: str,
    decision: str,
    resource: str | None = None,
    reason: str | None = None,
    policy_id: int | None = None,
    attributes: dict | None = None,
    ip: str | None = None,
) -> int | None:
    """Persist a single PDP decision. Safe to call with conn=None."""
    if conn is None:
        return None
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO policy_decisions "
                "(created_at, username, action, resource, decision, reason, "
                "policy_id, attributes_json, ip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _utcnow_iso(),
                    username,
                    action,
                    resource,
                    decision,
                    reason,
                    policy_id,
                    json.dumps(attributes) if attributes else None,
                    ip,
                ),
            )
            return cur.lastrowid
    except sqlite3.Error as exc:
        logger.warning("log_policy_decision failed: %s", exc)
        return None


def get_policy_decisions(
    conn: sqlite3.Connection,
    *,
    limit: int = 200,
    offset: int = 0,
    username: str | None = None,
    decision: str | None = None,
) -> list[dict]:
    if conn is None:
        return []
    try:
        sql = (
            "SELECT id, created_at, username, action, resource, decision, reason, "
            "policy_id, attributes_json, ip FROM policy_decisions"
        )
        clauses: list[str] = []
        params: list = []
        if username:
            clauses.append("username = ?")
            params.append(username)
        if decision in ("permit", "deny"):
            clauses.append("decision = ?")
            params.append(decision)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        rows = conn.execute(sql, params).fetchall()
        out: list[dict] = []
        for r in rows:
            attrs = None
            if r[8]:
                try:
                    attrs = json.loads(r[8])
                except (TypeError, ValueError):
                    attrs = None
            out.append(
                {
                    "id": r[0],
                    "created_at": r[1],
                    "username": r[2],
                    "action": r[3],
                    "resource": r[4],
                    "decision": r[5],
                    "reason": r[6],
                    "policy_id": r[7],
                    "attributes": attrs,
                    "ip": r[9],
                }
            )
        return out
    except sqlite3.Error:
        return []
