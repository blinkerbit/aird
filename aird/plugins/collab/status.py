"""Whether a poll, list, or meeting is still active."""

from __future__ import annotations

from datetime import datetime, timezone


def poll_open(closes_at) -> bool:
    raw = str(closes_at or "").strip()
    if not raw:
        return True
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        end = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < end


def is_active(kind: str, detail: dict | None) -> bool:
    data = detail or {}
    if kind == "meet":
        return bool(data.get("live"))
    if kind == "poll":
        return poll_open(data.get("closes_at"))
    if kind == "list":
        if str(data.get("kind") or "todo") != "todo":
            return True
        prog = data.get("progress") or {}
        total = int(prog.get("total") or 0)
        done = int(prog.get("done") or 0)
        return total <= 0 or done < total
    return True
