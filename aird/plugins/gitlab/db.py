"""SQLite persistence for GitLab folder bindings and file comments."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from aird.constants.input_limits import FILE_COMMENT_MAX_LEN


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").strip("/")


def get_binding(
    conn: sqlite3.Connection, owner_username: str, folder_rel_path: str
) -> dict | None:
    folder = _norm(folder_rel_path)
    row = conn.execute(
        """
        SELECT owner_username, folder_rel_path, gitlab_host, code_project,
               issues_project, board_iid, repo_path_prefix, updated_by, updated_at
        FROM gitlab_bindings
        WHERE owner_username = ? AND folder_rel_path = ?
        """,
        (owner_username, folder),
    ).fetchone()
    if not row:
        return None
    keys = (
        "owner_username",
        "folder_rel_path",
        "gitlab_host",
        "code_project",
        "issues_project",
        "board_iid",
        "repo_path_prefix",
        "updated_by",
        "updated_at",
    )
    return dict(zip(keys, row))


def resolve_binding(
    conn: sqlite3.Connection, owner_username: str, folder_rel_path: str
) -> dict | None:
    """Nearest binding walking up from folder to root."""
    folder = _norm(folder_rel_path)
    parts = folder.split("/") if folder else []
    for i in range(len(parts), -1, -1):
        candidate = "/".join(parts[:i])
        found = get_binding(conn, owner_username, candidate)
        if found:
            return found
    return None


def upsert_binding(
    conn: sqlite3.Connection,
    *,
    owner_username: str,
    folder_rel_path: str,
    gitlab_host: str,
    code_project: str,
    issues_project: str,
    board_iid: int | None,
    repo_path_prefix: str,
    updated_by: str,
) -> dict:
    folder = _norm(folder_rel_path)
    now = _now()
    conn.execute(
        """
        INSERT INTO gitlab_bindings (
            owner_username, folder_rel_path, gitlab_host, code_project,
            issues_project, board_iid, repo_path_prefix, updated_by, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_username, folder_rel_path) DO UPDATE SET
            gitlab_host = excluded.gitlab_host,
            code_project = excluded.code_project,
            issues_project = excluded.issues_project,
            board_iid = excluded.board_iid,
            repo_path_prefix = excluded.repo_path_prefix,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        """,
        (
            owner_username,
            folder,
            (gitlab_host or "https://gitlab.com").strip(),
            (code_project or "").strip(),
            (issues_project or code_project or "").strip(),
            board_iid,
            (repo_path_prefix or "").strip().strip("/"),
            updated_by,
            now,
        ),
    )
    conn.commit()
    found = get_binding(conn, owner_username, folder)
    assert found is not None
    return found


def delete_binding(
    conn: sqlite3.Connection, owner_username: str, folder_rel_path: str
) -> bool:
    cur = conn.execute(
        "DELETE FROM gitlab_bindings WHERE owner_username = ? AND folder_rel_path = ?",
        (owner_username, _norm(folder_rel_path)),
    )
    conn.commit()
    return cur.rowcount > 0


def list_comments(
    conn: sqlite3.Connection, owner_username: str, file_rel_path: str
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, owner_username, file_rel_path, author_username, body,
               created_at, edited_at, gitlab_issue_iid, gitlab_note_id
        FROM file_comments
        WHERE owner_username = ? AND file_rel_path = ?
        ORDER BY created_at ASC, id ASC
        """,
        (owner_username, _norm(file_rel_path)),
    ).fetchall()
    keys = (
        "id",
        "owner_username",
        "file_rel_path",
        "author_username",
        "body",
        "created_at",
        "edited_at",
        "gitlab_issue_iid",
        "gitlab_note_id",
    )
    return [dict(zip(keys, row)) for row in rows]


def comment_counts(
    conn: sqlite3.Connection, owner_username: str, folder_rel_path: str, names: list[str]
) -> dict[str, int]:
    folder = _norm(folder_rel_path)
    counts = {name: 0 for name in names}
    if not names:
        return counts
    prefix = f"{folder}/" if folder else ""
    for name in names:
        exact = f"{prefix}{name}" if prefix else name
        row = conn.execute(
            """
            SELECT COUNT(*) FROM file_comments
            WHERE owner_username = ? AND file_rel_path = ?
            """,
            (owner_username, exact),
        ).fetchone()
        if row:
            counts[name] += int(row[0] or 0)
    rows = conn.execute(
        """
        SELECT file_rel_path, COUNT(*) FROM file_comments
        WHERE owner_username = ? AND file_rel_path LIKE ?
        GROUP BY file_rel_path
        """,
        (owner_username, prefix + "%"),
    ).fetchall()
    for rel, count in rows:
        rel_n = _norm(rel)
        if rel_n in {_norm(f"{prefix}{n}" if prefix else n) for n in names}:
            continue
        child = rel_n[len(prefix) :] if prefix and rel_n.startswith(prefix) else rel_n
        first = child.split("/", 1)[0]
        if first in counts:
            counts[first] += int(count)
    return counts


def insert_comment(
    conn: sqlite3.Connection,
    *,
    owner_username: str,
    file_rel_path: str,
    author_username: str,
    body: str,
    gitlab_issue_iid: int | None = None,
) -> dict:
    text = (body or "").strip()
    if not text:
        raise ValueError("Comment body is required")
    if len(text) > FILE_COMMENT_MAX_LEN:
        raise ValueError("Comment is too long")
    cid = str(uuid.uuid4())
    now = _now()
    conn.execute(
        """
        INSERT INTO file_comments (
            id, owner_username, file_rel_path, author_username, body,
            created_at, edited_at, gitlab_issue_iid, gitlab_note_id
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)
        """,
        (cid, owner_username, _norm(file_rel_path), author_username, text, now, gitlab_issue_iid),
    )
    conn.commit()
    return get_comment(conn, cid)


def get_comment(conn: sqlite3.Connection, comment_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT id, owner_username, file_rel_path, author_username, body,
               created_at, edited_at, gitlab_issue_iid, gitlab_note_id
        FROM file_comments WHERE id = ?
        """,
        (comment_id,),
    ).fetchone()
    if not row:
        return None
    keys = (
        "id",
        "owner_username",
        "file_rel_path",
        "author_username",
        "body",
        "created_at",
        "edited_at",
        "gitlab_issue_iid",
        "gitlab_note_id",
    )
    return dict(zip(keys, row))


def update_comment(
    conn: sqlite3.Connection,
    comment_id: str,
    *,
    body: str | None = None,
    gitlab_issue_iid: int | None = None,
    gitlab_note_id: int | None = None,
    set_promote: bool = False,
) -> dict | None:
    current = get_comment(conn, comment_id)
    if not current:
        return None
    new_body = current["body"]
    if body is not None:
        text = body.strip()
        if not text:
            raise ValueError("Comment body is required")
        if len(text) > FILE_COMMENT_MAX_LEN:
            raise ValueError("Comment is too long")
        new_body = text
    issue = current["gitlab_issue_iid"]
    note = current["gitlab_note_id"]
    if set_promote:
        issue = gitlab_issue_iid
        note = gitlab_note_id
    edited = _now() if body is not None else current["edited_at"]
    conn.execute(
        """
        UPDATE file_comments
        SET body = ?, edited_at = ?, gitlab_issue_iid = ?, gitlab_note_id = ?
        WHERE id = ?
        """,
        (new_body, edited, issue, note, comment_id),
    )
    conn.commit()
    return get_comment(conn, comment_id)


def delete_comment(conn: sqlite3.Connection, comment_id: str) -> bool:
    cur = conn.execute("DELETE FROM file_comments WHERE id = ?", (comment_id,))
    conn.commit()
    return cur.rowcount > 0
