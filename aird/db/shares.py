"""Share database operations."""

import json
import logging
import secrets
import sqlite3
import traceback
from datetime import datetime

from aird.db.schema import PRAGMA_TABLE_INFO
from aird.sql_identifiers import (
    format_select_columns,
    format_shares_select_by_id_sql,
    format_shares_select_sql,
    format_update_by_id_sql,
)

logger = logging.getLogger(__name__)

_SHARE_BASE_COLS = ["id", "created", "paths", "allowed_users"]
_SHARE_OPTIONAL_COLS = [
    "modify_users",
    "secret_token",
    "share_type",
    "allow_list",
    "avoid_list",
    "expiry_date",
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
) -> bool:
    try:
        with conn:
            conn.execute(
                "REPLACE INTO shares (id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date, modify_users) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    allow_list: list = None,
    avoid_list: list = None,
    secret_token: str = None,
    expiry_date: str = None,
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

        if allow_list is not None:
            updates.append("allow_list = ?")
            values.append(json.dumps(allow_list) if allow_list else None)

        if avoid_list is not None:
            updates.append("avoid_list = ?")
            values.append(json.dumps(avoid_list) if avoid_list else None)

        if expiry_date is not None:
            updates.append("expiry_date = ?")
            values.append(expiry_date)

        kwarg_updates, kwarg_values = _get_kwargs_field_updates(kwargs)
        updates.extend(kwarg_updates)
        values.extend(kwarg_values)

        if not updates:
            return False

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
    """Check if a share has expired based on expiry_date using system time"""
    if not expiry_date:
        return False

    try:
        expiry_datetime = datetime.fromisoformat(expiry_date.replace("Z", ""))
        current_datetime = datetime.now()
        is_expired = current_datetime > expiry_datetime
        logging.debug(
            f"Checking expiry: current={current_datetime}, expiry={expiry_datetime}, expired={is_expired}"
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


def get_shares_for_path(conn: sqlite3.Connection, file_path: str) -> list:
    """Get all shares that contain a specific file path"""
    try:
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        available = [row[1] for row in cursor.fetchall()]
        col_names = _get_share_col_names(available)
        cols = format_select_columns(col_names, _SHARE_SELECT_COLS_ALLOWED)
        cursor = conn.execute(format_shares_select_sql(cols))
        matching = []
        for row in cursor:
            share = _row_to_share_dict(row, col_names)
            if file_path in share["paths"]:
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
