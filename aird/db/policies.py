"""Policy definition database operations (ABAC PDP policy storage)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _serialise_actions(actions) -> str:
    if isinstance(actions, str):
        actions = [actions]
    return json.dumps(list(actions or []))


def _deserialise_actions(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [str(x) for x in parsed]
    return []


def _row_to_policy(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "effect": row[3],
        "target_actions": _deserialise_actions(row[4]),
        "condition": _safe_json_loads(row[5]),
        "priority": row[6],
        "enabled": bool(row[7]),
        "created_at": row[8],
        "updated_at": row[9],
    }


def _safe_json_loads(raw: str | None):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def insert_policy(
    conn: sqlite3.Connection,
    *,
    name: str,
    effect: str,
    target_actions,
    condition: dict,
    description: str | None = None,
    priority: int = 0,
    enabled: bool = True,
) -> int | None:
    if conn is None or not name or effect not in ("permit", "deny"):
        return None
    now = _utcnow_iso()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO policies (name, description, effect, target_actions, "
                "condition_json, priority, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    description,
                    effect,
                    _serialise_actions(target_actions),
                    json.dumps(condition or {}),
                    int(priority),
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    except sqlite3.Error as exc:
        logger.warning("insert_policy failed: %s", exc)
        return None


def update_policy(conn: sqlite3.Connection, policy_id: int, **fields) -> bool:
    if conn is None or policy_id is None:
        return False
    allowed = {
        "name",
        "description",
        "effect",
        "target_actions",
        "condition",
        "priority",
        "enabled",
    }
    updates: list[str] = []
    values: list = []
    for key, raw_value in fields.items():
        if key not in allowed:
            continue
        if key == "target_actions":
            updates.append("target_actions = ?")
            values.append(_serialise_actions(raw_value))
        elif key == "condition":
            updates.append("condition_json = ?")
            values.append(json.dumps(raw_value or {}))
        elif key == "enabled":
            updates.append("enabled = ?")
            values.append(1 if raw_value else 0)
        elif key == "priority":
            updates.append("priority = ?")
            values.append(int(raw_value))
        elif key == "effect":
            if raw_value not in ("permit", "deny"):
                return False
            updates.append("effect = ?")
            values.append(raw_value)
        else:
            updates.append(f"{key} = ?")
            values.append(raw_value)
    if not updates:
        return False
    updates.append("updated_at = ?")
    values.append(_utcnow_iso())
    values.append(int(policy_id))
    try:
        with conn:
            cur = conn.execute(
                f"UPDATE policies SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0
    except sqlite3.Error as exc:
        logger.warning("update_policy failed: %s", exc)
        return False


def delete_policy(conn: sqlite3.Connection, policy_id: int) -> bool:
    if conn is None or policy_id is None:
        return False
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM policies WHERE id = ?", (int(policy_id),)
            )
            return cur.rowcount > 0
    except sqlite3.Error:
        return False


def get_policy(conn: sqlite3.Connection, policy_id: int) -> dict | None:
    if conn is None or policy_id is None:
        return None
    try:
        row = conn.execute(
            "SELECT id, name, description, effect, target_actions, condition_json, "
            "priority, enabled, created_at, updated_at FROM policies WHERE id = ?",
            (int(policy_id),),
        ).fetchone()
        return _row_to_policy(row) if row else None
    except sqlite3.Error:
        return None


def get_policy_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    if conn is None or not name:
        return None
    try:
        row = conn.execute(
            "SELECT id, name, description, effect, target_actions, condition_json, "
            "priority, enabled, created_at, updated_at FROM policies WHERE name = ?",
            (name,),
        ).fetchone()
        return _row_to_policy(row) if row else None
    except sqlite3.Error:
        return None


def list_policies(
    conn: sqlite3.Connection, *, enabled_only: bool = False
) -> list[dict]:
    if conn is None:
        return []
    try:
        sql = (
            "SELECT id, name, description, effect, target_actions, condition_json, "
            "priority, enabled, created_at, updated_at FROM policies"
        )
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY priority DESC, id ASC"
        rows = conn.execute(sql).fetchall()
        return [_row_to_policy(r) for r in rows]
    except sqlite3.Error:
        return []
