"""Pure helpers for scrum swimlane age and 10-day assignee load."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def days_since(ts: str | None, *, now: datetime | None = None) -> float | None:
    parsed = _parse_ts(ts)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - parsed).total_seconds() / 86400.0)


def last_comment_at(notes: list[dict[str, Any]] | None) -> str | None:
    latest = None
    latest_dt = None
    for note in notes or []:
        if note.get("system"):
            continue
        created = note.get("created_at") or note.get("updated_at")
        dt = _parse_ts(created)
        if dt is None:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest = created
    return latest


def days_in_lane(
    label_events: list[dict[str, Any]] | None,
    current_labels: list[str] | None,
    *,
    fallback_created_at: str | None = None,
    now: datetime | None = None,
) -> float | None:
    """Age of the current board list, using the last matching add-label event."""
    labels = {str(x).lower() for x in (current_labels or []) if x}
    moved_at = None
    for event in label_events or []:
        action = str(event.get("action") or "").lower()
        if action not in {"add", "added"}:
            continue
        label = event.get("label") or {}
        name = str(label.get("name") or event.get("label_name") or "").lower()
        if labels and name not in labels:
            continue
        created = event.get("created_at")
        dt = _parse_ts(created)
        if dt is None:
            continue
        if moved_at is None or dt > moved_at:
            moved_at = dt
    if moved_at is None:
        return days_since(fallback_created_at, now=now)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - moved_at).total_seconds() / 86400.0)


def hours_for_issue(
    issue: dict[str, Any],
    *,
    hours_per_weight: float = 8.0,
) -> float:
    stats = issue.get("time_stats") or {}
    estimate = stats.get("time_estimate")
    try:
        if estimate:
            return max(0.0, float(estimate) / 3600.0)
    except (TypeError, ValueError):
        pass
    weight = issue.get("weight")
    try:
        if weight is not None:
            return max(0.0, float(weight) * float(hours_per_weight))
    except (TypeError, ValueError):
        pass
    return 0.0


def assignee_usernames(issue: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for person in issue.get("assignees") or []:
        name = person.get("username") or person.get("name")
        if name:
            names.append(str(name))
    single = issue.get("assignee") or {}
    name = single.get("username") or single.get("name")
    if name and str(name) not in names:
        names.append(str(name))
    return names or ["unassigned"]


def busyness_for_issues(
    issues: list[dict[str, Any]],
    *,
    window_days: int = 10,
    hours_per_day: float = 8.0,
    hours_per_weight: float = 8.0,
) -> dict[str, Any]:
    capacity = max(1.0, float(window_days) * float(hours_per_day))
    by_user: dict[str, float] = {}
    for issue in issues:
        state = str(issue.get("state") or "opened").lower()
        if state not in {"opened", "open"}:
            continue
        hours = hours_for_issue(issue, hours_per_weight=hours_per_weight)
        people = assignee_usernames(issue)
        share = hours / len(people)
        for name in people:
            by_user[name] = by_user.get(name, 0.0) + share
    users = []
    for name, allocated in sorted(by_user.items(), key=lambda kv: (-kv[1], kv[0])):
        free = max(0.0, capacity - allocated)
        users.append(
            {
                "username": name,
                "allocated_hours": round(allocated, 2),
                "free_hours": round(free, 2),
                "capacity_hours": round(capacity, 2),
                "load_ratio": round(allocated / capacity, 3),
            }
        )
    return {
        "window_days": window_days,
        "hours_per_day": hours_per_day,
        "capacity_hours": round(capacity, 2),
        "users": users,
    }
