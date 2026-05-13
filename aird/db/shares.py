"""Share database operations."""

import functools
import json
import logging
import secrets
import sqlite3
import traceback
from datetime import datetime, timezone

import os

from aird.db.schema import PRAGMA_TABLE_INFO
from aird.sql_identifiers import (
    format_select_columns,
    format_shares_select_by_id_sql,
    format_shares_select_sql,
    format_update_by_id_sql,
)
from aird.constants import ROOT_DIR
from aird.core.file_operations import (
    filter_files_by_patterns,
    get_files_by_tag_patterns,
    remove_share_cloud_dir,
)
from aird.core.security import is_within_root
from aird.core.share_root import filesystem_root_for_share
from aird.db.resource_tags import list_resource_tags

logger = logging.getLogger(__name__)

# Sentinel: omit allow_list / avoid_list / expiry_date from UPDATE (vs explicit None → SQL NULL).
_NO_LIST_OR_EXPIRY_UPDATE = object()

_SHARE_BASE_COLS = ["id", "created", "paths", "allowed_users"]
_SHARE_OPTIONAL_COLS = [
    "modify_users",
    "secret_token",
    "share_type",
    "allow_list",
    "avoid_list",
    "expiry_date",
    "tag_name",
    "created_by",
]
_SHARE_SELECT_COLS_ALLOWED = frozenset(_SHARE_BASE_COLS + _SHARE_OPTIONAL_COLS)


def insert_share(
    conn: sqlite3.Connection,
    sid: str,
    created: str,
    paths: list[str],
    allowed_users: list[str] = None,
    secret_token: str = None,
    share_type: str = "static",
    allow_list: list[str] = None,
    avoid_list: list[str] = None,
    expiry_date: str = None,
    modify_users: list[str] = None,
    tag_name: str = None,
    created_by: str = None,
) -> bool:
    try:
        with conn:
            conn.execute(
                "REPLACE INTO shares (id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date, modify_users, tag_name, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sid,
                    created,
                    json.dumps(paths),
                    json.dumps(allowed_users) if allowed_users else None,
                    secret_token,
                    share_type,
                    json.dumps(allow_list) if allow_list else None,
                    json.dumps(avoid_list) if avoid_list else None,
                    expiry_date,
                    json.dumps(modify_users) if modify_users else None,
                    tag_name,
                    created_by,
                ),
            )
        return True
    except Exception as e:
        logging.error(f"Failed to insert share {sid} into database: {e}")
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False


def delete_share(conn: sqlite3.Connection, sid: str) -> None:
    try:
        with conn:
            conn.execute("DELETE FROM shares WHERE id = ?", (sid,))
        remove_share_cloud_dir(sid)
    except Exception:
        logger.debug("delete_share failed for %s", sid, exc_info=True)


def _get_token_field_updates(
    disable_token: bool | None, secret_token: str | None
) -> tuple[list, list]:
    """Return (SET clauses, values) for secret_token update."""
    if disable_token is None and secret_token is None:
        return [], []
    updates, values = [], []
    if disable_token is True:
        updates.append("secret_token = ?")
        values.append(None)
    else:
        new_token = secrets.token_urlsafe(64) if secret_token is None else secret_token
        updates.append("secret_token = ?")
        values.append(new_token)
    return updates, values


def _get_kwargs_field_updates(kwargs: dict) -> tuple[list, list]:
    """Return (SET clauses, values) for legacy allowed_users/paths kwargs."""
    field_sql = {
        "allowed_users": "allowed_users = ?",
        "modify_users": "modify_users = ?",
        "paths": "paths = ?",
    }
    updates, values = [], []
    for field, value in kwargs.items():
        if field in field_sql:
            updates.append(field_sql[field])
            values.append(json.dumps(value) if value is not None else value)
    return updates, values


def update_share(
    conn: sqlite3.Connection,
    sid: str,
    share_type: str = None,
    disable_token: bool = None,
    allow_list: list | object = _NO_LIST_OR_EXPIRY_UPDATE,
    avoid_list: list | object = _NO_LIST_OR_EXPIRY_UPDATE,
    secret_token: str = None,
    expiry_date: str | object = _NO_LIST_OR_EXPIRY_UPDATE,
    tag_name: str = None,
    **kwargs,
) -> bool:
    """Update share information"""
    try:
        updates, values = [], []

        if share_type is not None:
            updates.append("share_type = ?")
            values.append(share_type)

        token_updates, token_values = _get_token_field_updates(
            disable_token, secret_token
        )
        updates.extend(token_updates)
        values.extend(token_values)

        if allow_list is not _NO_LIST_OR_EXPIRY_UPDATE:
            updates.append("allow_list = ?")
            values.append(json.dumps(allow_list) if allow_list else None)

        if avoid_list is not _NO_LIST_OR_EXPIRY_UPDATE:
            updates.append("avoid_list = ?")
            values.append(json.dumps(avoid_list) if avoid_list else None)

        if expiry_date is not _NO_LIST_OR_EXPIRY_UPDATE:
            updates.append("expiry_date = ?")
            values.append(expiry_date)

        if tag_name is not None:
            updates.append("tag_name = ?")
            values.append(tag_name)

        kwarg_updates, kwarg_values = _get_kwargs_field_updates(kwargs)
        updates.extend(kwarg_updates)
        values.extend(kwarg_values)

        if not updates:
            return True

        values.append(sid)
        query = format_update_by_id_sql("shares", ", ".join(updates))

        with conn:
            cursor = conn.execute(query, values)
            logging.debug(f"Update executed, rows affected: {cursor.rowcount}")

        return True
    except Exception as e:
        logging.error(f"Failed to update share {sid}: {e}")
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False


def is_share_expired(expiry_date: str) -> bool:
    """Check if a share has expired based on expiry_date (UTC-aware compare)."""
    if not expiry_date:
        return False

    try:
        raw = expiry_date.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        expiry_datetime = datetime.fromisoformat(raw)
        if expiry_datetime.tzinfo is None:
            expiry_datetime = expiry_datetime.replace(tzinfo=timezone.utc)
        current_utc = datetime.now(timezone.utc)
        is_expired = current_utc > expiry_datetime.astimezone(timezone.utc)
        logging.debug(
            "Checking expiry: current=%s, expiry=%s, expired=%s",
            current_utc,
            expiry_datetime,
            is_expired,
        )
        return is_expired
    except Exception as e:
        logging.error(f"Error checking expiry date {expiry_date}: {e}")
        return False


def cleanup_expired_shares(conn: sqlite3.Connection) -> int:
    """Remove expired shares from the database. Returns the number of shares deleted."""
    try:
        cursor = conn.execute(
            "SELECT id, expiry_date FROM shares WHERE expiry_date IS NOT NULL"
        )
        rows = cursor.fetchall()

        deleted_count = 0
        for share_id, expiry_date in rows:
            if is_share_expired(expiry_date):
                with conn:
                    conn.execute("DELETE FROM shares WHERE id = ?", (share_id,))
                    deleted_count += 1
                    logging.info(f"Deleted expired share: {share_id}")

        return deleted_count
    except Exception as e:
        logging.error(f"Error cleaning up expired shares: {e}")
        return 0


def _get_share_col_names(available_columns: list) -> list:
    """Return column list for SELECT based on available schema columns."""
    return _SHARE_BASE_COLS + [
        c for c in _SHARE_OPTIONAL_COLS if c in available_columns
    ]


def _row_to_share_dict(row: tuple, col_names: list) -> dict:
    """Convert a raw share DB row to a normalized share dict."""
    d = dict(zip(col_names, row))
    return {
        "id": d["id"],
        "created": d["created"],
        "paths": json.loads(d["paths"]) if d.get("paths") else [],
        "allowed_users": (
            json.loads(d["allowed_users"]) if d.get("allowed_users") else None
        ),
        "modify_users": (
            json.loads(d["modify_users"]) if d.get("modify_users") else None
        ),
        "secret_token": d.get("secret_token"),
        "share_type": d.get("share_type") or "static",
        "allow_list": json.loads(d["allow_list"]) if d.get("allow_list") else [],
        "avoid_list": json.loads(d["avoid_list"]) if d.get("avoid_list") else [],
        "expiry_date": d.get("expiry_date"),
        "tag_name": d.get("tag_name"),
        "created_by": d.get("created_by"),
    }


def get_share_by_id(conn: sqlite3.Connection, sid: str) -> dict | None:
    """Get a single share by ID from database"""
    try:
        logging.debug(f"get_share_by_id called with sid='{sid}'")
        logging.debug(f"conn is {'None' if conn is None else 'available'}")
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        available = [row[1] for row in cursor.fetchall()]
        logging.debug(f"Table columns: {available}")
        col_names = _get_share_col_names(available)
        cols = format_select_columns(col_names, _SHARE_SELECT_COLS_ALLOWED)
        cursor = conn.execute(format_shares_select_by_id_sql(cols), (sid,))
        row = cursor.fetchone()
        if row:
            result = _row_to_share_dict(row, col_names)
            logging.debug(f"Returning share data: {result}")
            return result
        logging.debug(f"No share found for sid='{sid}'")
        return None
    except Exception as e:
        logging.error(f"Error getting share {sid}: {e}")
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return None


def get_all_shares(conn: sqlite3.Connection) -> dict:
    """Get all shares from database"""
    try:
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        available = [row[1] for row in cursor.fetchall()]
        col_names = _get_share_col_names(available)
        cols = format_select_columns(col_names, _SHARE_SELECT_COLS_ALLOWED)
        cursor = conn.execute(format_shares_select_sql(cols))
        return {row[0]: _row_to_share_dict(row, col_names) for row in cursor}
    except Exception as e:
        print(f"Error getting all shares: {e}")
        return {}


def list_shares_accessible_to_user(conn: sqlite3.Connection, username: str) -> list:
    """Return shares where *username* is in allowed_users (or share has no user restriction)."""
    if conn is None or not username:
        return []
    try:
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        available = [row[1] for row in cursor.fetchall()]
        col_names = _get_share_col_names(available)
        cols = format_select_columns(col_names, _SHARE_SELECT_COLS_ALLOWED)
        cursor = conn.execute(format_shares_select_sql(cols))
        result = []
        for row in cursor:
            share = _row_to_share_dict(row, col_names)
            allowed = share.get("allowed_users")
            is_creator = share.get("created_by") == username
            if allowed is None or username in allowed or is_creator:
                result.append(share)
        return result
    except Exception as e:
        logger.debug("list_shares_accessible_to_user failed: %s", e)
        return []


def _normalized_share_path(p: str) -> str:
    return str(p).replace("\\", "/")


@functools.lru_cache(maxsize=256)
def _cached_tag_raw_files(root_dir: str, patterns_key: str) -> tuple[str, ...]:
    """Memoize tag glob expansion per (filesystem root, sorted patterns json)."""
    patterns = json.loads(patterns_key)
    return tuple(get_files_by_tag_patterns(patterns, root_dir))


def _tag_patterns_key_from_conn(conn: sqlite3.Connection | None, tag_name: str) -> str | None:
    """JSON key of sorted glob patterns for *tag_name*, or None if unavailable."""
    if conn is None or not tag_name:
        return None
    try:
        rules = list_resource_tags(conn)
        patterns = sorted(
            [r["glob_pattern"] for r in rules if r.get("tag") == tag_name]
        )
        return json.dumps(patterns)
    except Exception:
        logger.debug("tag patterns key failed for %r", tag_name, exc_info=True)
        return None


def list_files_for_tag_share(
    conn: sqlite3.Connection | None,
    tag_name: str,
    root_dir: str,
    allow_list,
    avoid_list,
) -> list[str]:
    """All relative paths covered by a tag-type share (with allow/avoid filters)."""
    key = _tag_patterns_key_from_conn(conn, tag_name)
    if not key:
        return []
    matched = list(_cached_tag_raw_files(root_dir, key))
    return filter_files_by_patterns(matched, allow_list, avoid_list)


def share_covers_relative_path(
    conn: sqlite3.Connection | None,
    share: dict,
    rel_path: str,
    root_dir: str,
) -> bool:
    """True if *rel_path* (relative to *root_dir*) is covered by *share* (static, dynamic, or tag)."""
    share_type = share.get("share_type", "static")
    allow_list = share.get("allow_list", [])
    avoid_list = share.get("avoid_list", [])

    if share_type == "tag":
        tag_name = share.get("tag_name")
        key = _tag_patterns_key_from_conn(conn, tag_name)
        if not key:
            return False
        try:
            matched = list(_cached_tag_raw_files(root_dir, key))
            filtered = filter_files_by_patterns(matched, allow_list, avoid_list)
            return rel_path in filtered
        except Exception:
            logger.debug("share_covers_relative_path tag failed", exc_info=True)
            return False

    if share_type == "dynamic":
        for folder_path in share.get("paths") or []:
            try:
                full_folder_path = os.path.abspath(
                    os.path.join(root_dir, folder_path)
                )
                full_file_path = os.path.abspath(
                    os.path.join(root_dir, rel_path)
                )
                if (
                    os.path.isdir(full_folder_path)
                    and is_within_root(full_file_path, full_folder_path)
                    and filter_files_by_patterns(
                        [rel_path], allow_list, avoid_list
                    )
                ):
                    return True
            except Exception:
                continue
        return False

    if share_paths_cover_target(share.get("paths") or [], rel_path):
        return True
    filtered_paths = filter_files_by_patterns(
        share.get("paths") or [], allow_list, avoid_list
    )
    return rel_path in filtered_paths


def share_paths_cover_target(paths: list, target_path: str) -> bool:
    """True if *target_path* is a share root or is a directory prefix of some path in *paths*."""
    if not paths or not target_path:
        return False
    t = _normalized_share_path(target_path)
    prefix = f"{t}/"
    for p in paths:
        pn = _normalized_share_path(p)
        if pn == t or pn.startswith(prefix):
            return True
    return False


def get_shares_for_path(
    conn: sqlite3.Connection,
    file_path: str,
    root_dir: str | None = None,
) -> list:
    """Shares whose effective file set includes *file_path* (relative to each share's root).

    *root_dir* is kept for API compatibility; matching uses the per-share root
    from :func:`aird.core.share_root.filesystem_root_for_share`.
    """
    _ = root_dir  # legacy keyword; not used for matching
    try:
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        available = [row[1] for row in cursor.fetchall()]
        col_names = _get_share_col_names(available)
        cols = format_select_columns(col_names, _SHARE_SELECT_COLS_ALLOWED)
        cursor = conn.execute(format_shares_select_sql(cols))
        matching = []
        for row in cursor:
            share = _row_to_share_dict(row, col_names)
            if share_covers_relative_path(
                conn, share, file_path, filesystem_root_for_share(share)
            ):
                matching.append(share)
        return matching
    except Exception as e:
        print(f"Error getting shares for path {file_path}: {e}")
        return []


def get_share_download_count(conn: sqlite3.Connection, share_id: str) -> int:
    """Return the number of share_download audit events for a given share_id."""
    if conn is None:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='share_download' AND details LIKE ?",
            (f"share_id={share_id}%",),
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
