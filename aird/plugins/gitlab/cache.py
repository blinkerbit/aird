"""Cached GitLab issue metadata and path references (manual refresh)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from aird.plugins.gitlab.client import (
    get_issue,
    list_issue_links,
    list_issue_notes,
    list_open_issues,
)
from aird.plugins.gitlab.paths import bind_matches_name, collect_paths_from_text

_ISSUE_REF = re.compile(r"(?<![/\w])#(\d{1,8})\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_key(host: str, issues_project: str) -> str:
    return f"{(host or '').rstrip('/')}|{(issues_project or '').strip()}"


def _issue_refs(text: str | None) -> list[int]:
    if not text:
        return []
    seen: set[int] = set()
    out: list[int] = []
    for match in _ISSUE_REF.finditer(text):
        iid = int(match.group(1))
        if iid in seen:
            continue
        seen.add(iid)
        out.append(iid)
    return out


def _append_note_bodies(parts: list[str], notes: list[dict]) -> None:
    for note in notes:
        if note.get("system"):
            continue
        parts.append(str(note.get("body") or ""))


def _append_linked_issues(
    parts: list[str], host: str, token: str, project: str, iid: int, blob: str,
) -> None:
    for ref in _issue_refs(blob)[:12]:
        if ref == iid:
            continue
        linked = get_issue(host, token, project, ref)
        if linked:
            parts.append(linked.get("title") or "")
            parts.append(linked.get("description") or "")


def _append_issue_links(
    parts: list[str], host: str, token: str, project: str, iid: int,
) -> None:
    links = list_issue_links(host, token, project, iid) or []
    for link in links:
        parts.append(str(link.get("title") or ""))
        parts.append(str(link.get("description") or ""))


def _gather_issue_text(host: str, token: str, project: str, issue: dict) -> str:
    parts = [issue.get("title") or "", issue.get("description") or ""]
    iid = issue.get("iid")
    if not iid:
        return "\n".join(parts)
    iid_int = int(iid)
    notes = list_issue_notes(host, token, project, iid_int) or []
    _append_note_bodies(parts, notes)
    _append_linked_issues(parts, host, token, project, iid_int, "\n".join(parts))
    _append_issue_links(parts, host, token, project, iid_int)
    return "\n".join(parts)


def refresh_project_cache(
    conn: sqlite3.Connection,
    *,
    owner_username: str,
    host: str,
    issues_project: str,
    token: str,
) -> dict:
    """Pull open issues from GitLab and store path references."""
    key = project_key(host, issues_project)
    issues = list_open_issues(host, token, issues_project) or []
    seen_iids: set[int] = set()
    rows: list[tuple] = []
    for issue in issues:
        iid = issue.get("iid")
        if not iid:
            continue
        iid = int(iid)
        seen_iids.add(iid)
        blob = _gather_issue_text(host, token, issues_project, issue)
        paths = collect_paths_from_text(blob, host=host, project=issues_project)
        assignees = issue.get("assignees") or []
        labels = issue.get("labels") or []
        rows.append(
            (
                owner_username,
                key,
                iid,
                str(issue.get("title") or ""),
                str(issue.get("web_url") or ""),
                str(issue.get("state") or "opened"),
                json.dumps(paths),
                json.dumps(assignees),
                json.dumps(labels),
                _now(),
            )
        )
    conn.execute(
        "DELETE FROM gitlab_issue_cache WHERE owner_username = ? AND project_key = ?",
        (owner_username, key),
    )
    if rows:
        conn.executemany(
            """
            INSERT INTO gitlab_issue_cache (
                owner_username, project_key, issue_iid, title, web_url, state,
                paths_json, assignees_json, labels_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    conn.commit()
    return {
        "project_key": key,
        "issue_count": len(rows),
        "refreshed_at": _now(),
    }


def cache_meta(conn: sqlite3.Connection | None, owner_username: str, project_key: str) -> dict:
    if conn is None:
        return {"refreshed_at": None, "issue_count": 0}
    row = conn.execute(
        """
        SELECT COUNT(*), MAX(updated_at)
        FROM gitlab_issue_cache
        WHERE owner_username = ? AND project_key = ?
        """,
        (owner_username, project_key),
    ).fetchone()
    return {
        "issue_count": int(row[0] or 0) if row else 0,
        "refreshed_at": row[1] if row else None,
    }


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _cached_issue_row(row: tuple) -> dict:
    return {
        "iid": row[0],
        "title": row[1],
        "web_url": row[2],
        "state": row[3],
        "paths": _json_list(row[4]),
        "assignees": _json_list(row[5]),
        "labels": _json_list(row[6]),
        "updated_at": row[7],
    }


def list_cached_issues(
    conn: sqlite3.Connection | None,
    owner_username: str,
    project_key: str,
) -> list[dict]:
    if conn is None:
        return []
    rows = conn.execute(
        """
        SELECT issue_iid, title, web_url, state, paths_json, assignees_json, labels_json, updated_at
        FROM gitlab_issue_cache
        WHERE owner_username = ? AND project_key = ?
        ORDER BY issue_iid DESC
        """,
        (owner_username, project_key),
    ).fetchall()
    return [_cached_issue_row(row) for row in rows]


def issues_for_names(
    conn: sqlite3.Connection | None,
    owner_username: str,
    project_key: str,
    names: list[str],
    *,
    folder_rel: str,
    repo_prefix: str,
) -> dict[str, list[dict]]:
    hits: dict[str, list[dict]] = {n: [] for n in names}
    for issue in list_cached_issues(conn, owner_username, project_key):
        mapped = bind_matches_name(
            names,
            issue.get("paths") or [],
            folder_rel=folder_rel,
            repo_prefix=repo_prefix,
        )
        for name, paths in mapped.items():
            if not paths:
                continue
            hits[name].append(
                {
                    "iid": issue["iid"],
                    "title": issue["title"],
                    "web_url": issue["web_url"],
                    "state": issue["state"],
                    "paths": paths,
                }
            )
    return hits


def active_assignees(issues: list[dict]) -> list[dict]:
    seen: set[str] = set()
    users: list[dict] = []
    for issue in issues:
        for assignee in issue.get("assignees") or []:
            if not isinstance(assignee, dict):
                continue
            username = str(assignee.get("username") or "").strip()
            if not username or username in seen:
                continue
            seen.add(username)
            users.append(
                {
                    "username": username,
                    "name": assignee.get("name") or username,
                    "avatar_url": assignee.get("avatar_url"),
                }
            )
    return users
