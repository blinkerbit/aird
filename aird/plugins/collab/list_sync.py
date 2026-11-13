"""HLC timestamps, fractional order keys, and list block op apply."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from typing import Any


OPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS list_ops (
    op_id TEXT PRIMARY KEY,
    list_id TEXT NOT NULL,
    block_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    hlc TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_list_ops_list_hlc ON list_ops(list_id, hlc);
"""

ACTIONS = frozenset({"INSERT", "UPDATE_CONTENT", "MOVE", "CHANGE_TYPE", "DELETE", "TOGGLE"})


class HybridClock:
    def __init__(self) -> None:
        self.wall = 0
        self.logical = 0

    def tick(self) -> str:
        now = int(time.time() * 1000)
        if now > self.wall:
            self.wall = now
            self.logical = 0
        else:
            self.logical += 1
        return f"{self.wall:013d}:{self.logical:06d}"

    def receive(self, remote: str) -> str:
        wall, logical = _parse_hlc(remote)
        now = int(time.time() * 1000)
        if now > self.wall and now > wall:
            self.wall = now
            self.logical = 0
        elif wall > self.wall:
            self.wall = wall
            self.logical = logical + 1
        elif wall == self.wall:
            self.logical = max(self.logical, logical) + 1
        else:
            self.logical += 1
        return f"{self.wall:013d}:{self.logical:06d}"


def _parse_hlc(value: str) -> tuple[int, int]:
    raw = str(value or "").strip()
    if ":" not in raw:
        return 0, 0
    left, right = raw.split(":", 1)
    try:
        return int(left), int(right)
    except ValueError:
        return 0, 0


def hlc_cmp(a: str, b: str) -> int:
    aw, al = _parse_hlc(a)
    bw, bl = _parse_hlc(b)
    if aw != bw:
        return -1 if aw < bw else 1
    if al != bl:
        return -1 if al < bl else 1
    return 0


def mid_order(prev: str | None, next_key: str | None, actor: str = "") -> str:
    """Simple fractional key between neighbors; actor suffix disambiguates ties."""
    def to_num(key: str | None, default: float) -> float:
        if not key:
            return default
        head = str(key).split("#", 1)[0]
        try:
            return float(head)
        except ValueError:
            return default

    left = to_num(prev, 0.0)
    right = to_num(next_key, left + 2.0)
    if right <= left:
        right = left + 2.0
    mid = (left + right) / 2.0
    suffix = secrets.token_hex(2) if not actor else abs(hash(actor)) % 0xFFFF
    return f"{mid:.8f}#{suffix:04x}"


def block_type(value, fallback: str = "checkbox") -> str:
    raw = str(value or "").strip().lower()
    if raw in ("checkbox", "check", "todo"):
        return "checkbox"
    if raw == "bullet":
        return "bullet"
    if raw in ("paragraph", "note", "text"):
        return "paragraph"
    return fallback


def clip_content(value, limit: int = 8000) -> str:
    return str(value or "").strip()[:limit]


def _ordered_items(conn: sqlite3.Connection, list_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM list_items WHERE list_id = ? "
        "ORDER BY COALESCE(order_key, printf('%020d', sort_order)), created_at",
        (list_id,),
    ).fetchall()


def _descendants(items: list[sqlite3.Row], start_index: int) -> set[str]:
    """Blocks under start via contiguous deeper depths."""
    if start_index < 0 or start_index >= len(items):
        return set()
    base = int(items[start_index]["depth"] or 0)
    out = {items[start_index]["id"]}
    for row in items[start_index + 1 :]:
        depth = int(row["depth"] or 0)
        if depth <= base:
            break
        out.add(row["id"])
    return out


def apply_op(
    conn: sqlite3.Connection,
    list_id: str,
    op: dict[str, Any],
    *,
    clock: HybridClock | None = None,
) -> dict[str, Any]:
    """Apply one op idempotently. Returns {ok, reason?, op}."""
    op_id = str(op.get("opId") or op.get("op_id") or "").strip()
    if not op_id:
        return {"ok": False, "reason": "op_id required"}
    existing = conn.execute("SELECT op_id FROM list_ops WHERE op_id = ?", (op_id,)).fetchone()
    if existing:
        return {"ok": True, "reason": "duplicate", "op": _op_row(conn, op_id)}

    action = str(op.get("action") or "").upper()
    if action not in ACTIONS:
        return {"ok": False, "reason": "bad action"}
    block_id = str(op.get("blockId") or op.get("block_id") or "").strip()
    if not block_id:
        return {"ok": False, "reason": "block_id required"}
    actor = str(op.get("actorId") or op.get("actor_id") or "unknown").strip() or "unknown"
    payload = op.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}

    clock = clock or HybridClock()
    remote_hlc = str(op.get("hlcTimestamp") or op.get("hlc") or "")
    hlc = clock.receive(remote_hlc) if remote_hlc else clock.tick()
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())

    try:
        if action == "INSERT":
            _apply_insert(conn, list_id, block_id, payload, actor, now)
        elif action == "UPDATE_CONTENT":
            _apply_content(conn, list_id, block_id, payload, hlc, now)
        elif action == "CHANGE_TYPE":
            _apply_type(conn, list_id, block_id, payload, now)
        elif action == "TOGGLE":
            _apply_toggle(conn, list_id, block_id, payload, now)
        elif action == "MOVE":
            ok, reason = _apply_move(conn, list_id, block_id, payload, actor, now)
            if not ok:
                return {"ok": False, "reason": reason}
        elif action == "DELETE":
            conn.execute("DELETE FROM list_items WHERE id = ? AND list_id = ?", (block_id, list_id))
    except ValueError as err:
        return {"ok": False, "reason": str(err)}

    conn.execute(
        "INSERT INTO list_ops (op_id, list_id, block_id, actor_id, hlc, action, payload_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (op_id, list_id, block_id, actor, hlc, action, json.dumps(payload), now),
    )
    conn.execute("UPDATE collab_lists SET updated_at = ? WHERE id = ?", (now, list_id))
    return {
        "ok": True,
        "op": {
            "opId": op_id,
            "listId": list_id,
            "blockId": block_id,
            "actorId": actor,
            "hlcTimestamp": hlc,
            "action": action,
            "payload": payload,
        },
    }


def _apply_insert(conn, list_id, block_id, payload, actor, now) -> None:
    row = conn.execute(
        "SELECT id FROM list_items WHERE id = ? AND list_id = ?", (block_id, list_id)
    ).fetchone()
    if row:
        return
    items = _ordered_items(conn, list_id)
    after_id = payload.get("afterId") or payload.get("after_id")
    order = payload.get("order") or payload.get("order_key")
    depth = max(0, min(5, int(payload.get("depth") or 0)))
    typ = block_type(payload.get("type") or payload.get("kind"))
    content = clip_content(payload.get("content") or payload.get("text") or "")
    if not order:
        prev = None
        nxt = None
        if after_id:
            ids = [r["id"] for r in items]
            if after_id in ids:
                i = ids.index(after_id)
                prev = items[i]["order_key"] if "order_key" in items[i].keys() else None
                nxt = items[i + 1]["order_key"] if i + 1 < len(items) and "order_key" in items[i + 1].keys() else None
                prev_depth = int(items[i]["depth"] or 0) if "depth" in items[i].keys() else 0
                if depth > prev_depth + 1:
                    depth = prev_depth + 1
        elif items:
            prev = items[-1]["order_key"] if "order_key" in items[-1].keys() else None
        order = mid_order(prev, nxt, actor)
    sort_order = len(items)
    conn.execute(
        "INSERT INTO list_items (id, list_id, text, done, sort_order, labels_json, notes, due_at, remind_at, "
        "created_at, updated_at, kind, depth, order_key, collapsed, version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            block_id,
            list_id,
            content or " ",
            1 if typ == "checkbox" and payload.get("isChecked") else 0,
            sort_order,
            json.dumps(payload.get("tags") or payload.get("labels") or []),
            "",
            None,
            None,
            now,
            now,
            typ,
            depth,
            order,
            1 if payload.get("isCollapsed") else 0,
            1,
        ),
    )


def _field_hlc(conn, list_id, block_id, field: str) -> str:
    row = conn.execute(
        "SELECT hlc FROM list_ops WHERE list_id = ? AND block_id = ? AND action IN ('UPDATE_CONTENT','TOGGLE','CHANGE_TYPE') "
        "ORDER BY hlc DESC LIMIT 1",
        (list_id, block_id),
    ).fetchone()
    return row["hlc"] if row else ""


def _apply_content(conn, list_id, block_id, payload, hlc, now) -> None:
    item = conn.execute(
        "SELECT * FROM list_items WHERE id = ? AND list_id = ?", (block_id, list_id)
    ).fetchone()
    if not item:
        raise ValueError("missing block")
    prev = _field_hlc(conn, list_id, block_id, "content")
    if prev and hlc_cmp(hlc, prev) < 0:
        return
    text = clip_content(payload.get("content") if "content" in payload else payload.get("text"))
    if text is None or text == "":
        text = " "
    version = int(item["version"] or 1) + 1 if "version" in item.keys() else 1
    conn.execute(
        "UPDATE list_items SET text = ?, updated_at = ?, version = ? WHERE id = ?",
        (text, now, version, block_id),
    )


def _apply_type(conn, list_id, block_id, payload, now) -> None:
    typ = block_type(payload.get("type") or payload.get("kind"))
    done = 0 if typ != "checkbox" else None
    if done is None:
        conn.execute(
            "UPDATE list_items SET kind = ?, updated_at = ? WHERE id = ? AND list_id = ?",
            (typ, now, block_id, list_id),
        )
    else:
        conn.execute(
            "UPDATE list_items SET kind = ?, done = 0, due_at = NULL, remind_at = NULL, updated_at = ? "
            "WHERE id = ? AND list_id = ?",
            (typ, now, block_id, list_id),
        )


def _apply_toggle(conn, list_id, block_id, payload, now) -> None:
    if "isChecked" in payload:
        done = 1 if payload.get("isChecked") else 0
    else:
        row = conn.execute(
            "SELECT done FROM list_items WHERE id = ? AND list_id = ?", (block_id, list_id)
        ).fetchone()
        if not row:
            raise ValueError("missing block")
        done = 0 if row["done"] else 1
    conn.execute(
        "UPDATE list_items SET done = ?, updated_at = ? WHERE id = ? AND list_id = ? AND kind = 'checkbox'",
        (done, now, block_id, list_id),
    )


def _apply_move(conn, list_id, block_id, payload, actor, now) -> tuple[bool, str]:
    items = _ordered_items(conn, list_id)
    ids = [r["id"] for r in items]
    if block_id not in ids:
        return False, "missing block"
    index = ids.index(block_id)
    parent_id = payload.get("parentBlockId") if "parentBlockId" in payload else payload.get("parent_block_id")
    depth = payload.get("depth")
    order = payload.get("order") or payload.get("order_key")
    after_id = payload.get("afterId") or payload.get("after_id")

    if parent_id:
        if parent_id not in ids:
            depth = 0
            parent_id = None
        else:
            parent_index = ids.index(parent_id)
            if block_id in _descendants(items, parent_index) and parent_id != block_id:
                # cycle: parent is under block
                if parent_id in _descendants(items, index):
                    depth = 0
                    parent_id = None
            if parent_id is not None:
                parent_depth = int(items[parent_index]["depth"] or 0)
                depth = parent_depth + 1

    if depth is None:
        depth = int(items[index]["depth"] or 0)
    depth = max(0, min(5, int(depth)))

    if after_id and after_id in ids and after_id != block_id:
        after_i = ids.index(after_id)
        prev_key = items[after_i]["order_key"] if "order_key" in items[after_i].keys() else None
        nxt_key = None
        for j in range(after_i + 1, len(items)):
            if items[j]["id"] == block_id:
                continue
            nxt_key = items[j]["order_key"] if "order_key" in items[j].keys() else None
            break
        order = mid_order(prev_key, nxt_key, actor)
        prev_depth = int(items[after_i]["depth"] or 0)
        if depth > prev_depth + 1:
            depth = prev_depth + 1
    elif not order:
        order = items[index]["order_key"] if "order_key" in items[index].keys() else mid_order(None, None, actor)

    collapsed = payload.get("isCollapsed")
    if collapsed is None:
        conn.execute(
            "UPDATE list_items SET depth = ?, order_key = ?, updated_at = ? WHERE id = ? AND list_id = ?",
            (depth, order, now, block_id, list_id),
        )
    else:
        conn.execute(
            "UPDATE list_items SET depth = ?, order_key = ?, collapsed = ?, updated_at = ? WHERE id = ? AND list_id = ?",
            (depth, order, 1 if collapsed else 0, now, block_id, list_id),
        )
    return True, ""


def _op_row(conn, op_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM list_ops WHERE op_id = ?", (op_id,)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "opId": row["op_id"],
        "listId": row["list_id"],
        "blockId": row["block_id"],
        "actorId": row["actor_id"],
        "hlcTimestamp": row["hlc"],
        "action": row["action"],
        "payload": payload,
    }


def sync_list(
    conn: sqlite3.Connection,
    list_id: str,
    *,
    actor_id: str,
    since_hlc: str = "",
    ops: list[dict] | None = None,
) -> dict:
    clock = HybridClock()
    tip = conn.execute(
        "SELECT hlc FROM list_ops WHERE list_id = ? ORDER BY hlc DESC LIMIT 1",
        (list_id,),
    ).fetchone()
    if tip:
        clock.receive(tip["hlc"])

    applied = []
    rejected = []
    for raw in ops or []:
        result = apply_op(conn, list_id, raw, clock=clock)
        if result.get("ok"):
            if result.get("op"):
                applied.append(result["op"])
        else:
            rejected.append({"opId": raw.get("opId") or raw.get("op_id"), "reason": result.get("reason")})

    remote = []
    query = "SELECT * FROM list_ops WHERE list_id = ?"
    args: list[Any] = [list_id]
    if since_hlc:
        query += " AND hlc > ?"
        args.append(since_hlc)
    query += " ORDER BY hlc ASC LIMIT 2000"
    for row in conn.execute(query, args).fetchall():
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        remote.append(
            {
                "opId": row["op_id"],
                "listId": row["list_id"],
                "blockId": row["block_id"],
                "actorId": row["actor_id"],
                "hlcTimestamp": row["hlc"],
                "action": row["action"],
                "payload": payload,
            }
        )
    conn.commit()
    tip_hlc = remote[-1]["hlcTimestamp"] if remote else (tip["hlc"] if tip else clock.tick())
    return {
        "applied": applied,
        "rejected": rejected,
        "remote_ops": remote,
        "tip_hlc": tip_hlc,
        "actor_id": actor_id,
    }
