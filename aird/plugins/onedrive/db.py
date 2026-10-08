"""Per-user OneDrive backup path rules."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

_MAX_PATHS = 200
_MAX_PATH_LEN = 4096


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_paths(paths: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        rel = str(raw or "").replace("\\", "/").strip().lstrip("/")
        if not rel or rel.startswith("../") or "/../" in rel or rel == "..":
            continue
        if len(rel) > _MAX_PATH_LEN:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
        if len(out) >= _MAX_PATHS:
            break
    return out


def get_rules(conn: sqlite3.Connection | None, username: str) -> dict:
    default = {
        "paths": [],
        "include_untracked": False,
        "include_aird_config": False,
    }
    if conn is None or not username:
        return default
    row = conn.execute(
        """
        SELECT paths_json, include_untracked, include_aird_config
        FROM onedrive_backup_rules WHERE username = ?
        """,
        (username,),
    ).fetchone()
    if not row:
        return default
    try:
        paths = json.loads(row[0] or "[]")
    except json.JSONDecodeError:
        paths = []
    if not isinstance(paths, list):
        paths = []
    return {
        "paths": _normalize_paths(paths),
        "include_untracked": bool(row[1]),
        "include_aird_config": bool(row[2]),
    }


def save_rules(
    conn: sqlite3.Connection,
    username: str,
    *,
    paths: list[str] | None = None,
    include_untracked: bool = True,
    include_aird_config: bool = True,
) -> dict:
    names = _normalize_paths(paths)
    conn.execute(
        """
        INSERT INTO onedrive_backup_rules (
            username, paths_json, include_untracked, include_aird_config, updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            paths_json = excluded.paths_json,
            include_untracked = excluded.include_untracked,
            include_aird_config = excluded.include_aird_config,
            updated_at = excluded.updated_at
        """,
        (
            username,
            json.dumps(names),
            1 if include_untracked else 0,
            1 if include_aird_config else 0,
            _now(),
        ),
    )
    conn.commit()
    return get_rules(conn, username)


def mark_paths(
    conn: sqlite3.Connection,
    username: str,
    paths: list[str] | None,
    *,
    marked: bool = True,
) -> dict:
    rules = get_rules(conn, username)
    current = list(rules["paths"])
    incoming = _normalize_paths(paths)
    if marked:
        seen = set(current)
        for rel in incoming:
            if rel not in seen:
                current.append(rel)
                seen.add(rel)
                if len(current) >= _MAX_PATHS:
                    break
    else:
        drop = set(incoming)
        current = [rel for rel in current if rel not in drop]
    return save_rules(
        conn,
        username,
        paths=current,
        include_untracked=rules["include_untracked"],
        include_aird_config=rules["include_aird_config"],
    )


def get_file_state(
    conn: sqlite3.Connection | None, username: str, rel_path: str
) -> tuple[float, int] | None:
    if conn is None or not username or not rel_path:
        return None
    row = conn.execute(
        """
        SELECT local_mtime, local_size FROM onedrive_sync_state
        WHERE username = ? AND rel_path = ?
        """,
        (username, rel_path),
    ).fetchone()
    if not row:
        return None
    try:
        return (float(row[0]), int(row[1]))
    except (TypeError, ValueError):
        return None


def set_file_state(
    conn: sqlite3.Connection,
    username: str,
    rel_path: str,
    *,
    local_mtime: float,
    local_size: int,
    remote_item_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO onedrive_sync_state (
            username, rel_path, local_mtime, local_size, remote_item_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(username, rel_path) DO UPDATE SET
            local_mtime = excluded.local_mtime,
            local_size = excluded.local_size,
            remote_item_id = excluded.remote_item_id,
            updated_at = excluded.updated_at
        """,
        (username, rel_path, local_mtime, local_size, remote_item_id or "", _now()),
    )
    conn.commit()


def get_run_status(conn: sqlite3.Connection | None, username: str) -> dict:
    empty = {
        "last_started_at": None,
        "last_finished_at": None,
        "last_ok": False,
        "last_error": None,
        "uploaded": 0,
        "skipped": 0,
        "failed": 0,
    }
    if conn is None or not username:
        return empty
    row = conn.execute(
        """
        SELECT last_started_at, last_finished_at, last_ok, last_error,
               uploaded, skipped, failed
        FROM onedrive_sync_status WHERE username = ?
        """,
        (username,),
    ).fetchone()
    if not row:
        return empty
    return {
        "last_started_at": row[0],
        "last_finished_at": row[1],
        "last_ok": bool(row[2]),
        "last_error": row[3],
        "uploaded": int(row[4] or 0),
        "skipped": int(row[5] or 0),
        "failed": int(row[6] or 0),
    }


def set_run_status(
    conn: sqlite3.Connection,
    username: str,
    *,
    last_started_at: str | None = None,
    last_finished_at: str | None = None,
    last_ok: bool = False,
    last_error: str | None = None,
    uploaded: int = 0,
    skipped: int = 0,
    failed: int = 0,
) -> dict:
    conn.execute(
        """
        INSERT INTO onedrive_sync_status (
            username, last_started_at, last_finished_at, last_ok, last_error,
            uploaded, skipped, failed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            last_started_at = excluded.last_started_at,
            last_finished_at = excluded.last_finished_at,
            last_ok = excluded.last_ok,
            last_error = excluded.last_error,
            uploaded = excluded.uploaded,
            skipped = excluded.skipped,
            failed = excluded.failed
        """,
        (
            username,
            last_started_at,
            last_finished_at,
            1 if last_ok else 0,
            last_error,
            uploaded,
            skipped,
            failed,
        ),
    )
    conn.commit()
    return get_run_status(conn, username)


def usernames_with_onedrive_token(conn: sqlite3.Connection | None) -> list[str]:
    if conn is None:
        return []
    from aird.db.plugin_secrets import SECRET_ONEDRIVE

    rows = conn.execute(
        "SELECT username FROM user_plugin_secrets WHERE secret_key = ?",
        (SECRET_ONEDRIVE,),
    ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


_MAX_MAPS = 50
_MAX_IGNORE_CHARS = 8000


def _normalize_remote(path: str | None) -> str:
    rel = str(path or "").replace("\\", "/").strip().strip("/")
    if not rel or rel.startswith("../") or "/../" in rel or rel == "..":
        return ""
    if len(rel) > _MAX_PATH_LEN:
        return ""
    return rel


def list_maps(conn: sqlite3.Connection | None, username: str) -> list[dict]:
    if conn is None or not username:
        return []
    rows = conn.execute(
        """
        SELECT id, local_path, remote_path, ignore_extra, updated_at
        FROM onedrive_folder_maps WHERE username = ? ORDER BY id
        """,
        (username,),
    ).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": int(row[0]),
                "local_path": row[1],
                "remote_path": row[2],
                "ignore_extra": row[3] or "",
                "updated_at": row[4],
            }
        )
    return out


def save_map(
    conn: sqlite3.Connection,
    username: str,
    *,
    map_id: int | None = None,
    local_path: str,
    remote_path: str,
    ignore_extra: str = "",
) -> dict:
    locals_ = _normalize_paths([local_path])
    remote = _normalize_remote(remote_path)
    if not locals_ or not remote:
        raise ValueError("local_path and remote_path are required")
    extra = (ignore_extra or "")[:_MAX_IGNORE_CHARS]
    existing = list_maps(conn, username)
    if map_id is None and len(existing) >= _MAX_MAPS:
        raise ValueError("Too many folder maps")
    if map_id is None:
        conn.execute(
            """
            INSERT INTO onedrive_folder_maps (
                username, local_path, remote_path, ignore_extra, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username, local_path) DO UPDATE SET
                remote_path = excluded.remote_path,
                ignore_extra = excluded.ignore_extra,
                updated_at = excluded.updated_at
            """,
            (username, locals_[0], remote, extra, _now()),
        )
    else:
        conn.execute(
            """
            UPDATE onedrive_folder_maps
            SET local_path = ?, remote_path = ?, ignore_extra = ?, updated_at = ?
            WHERE id = ? AND username = ?
            """,
            (locals_[0], remote, extra, _now(), int(map_id), username),
        )
    conn.commit()
    for item in list_maps(conn, username):
        if item["local_path"] == locals_[0]:
            return item
    raise RuntimeError("Folder map was not saved")


def delete_map(conn: sqlite3.Connection, username: str, map_id: int) -> None:
    conn.execute(
        "DELETE FROM onedrive_folder_maps WHERE id = ? AND username = ?",
        (int(map_id), username),
    )
    conn.commit()


