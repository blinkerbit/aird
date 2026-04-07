"""Share management database functions."""

import json
import logging
import sqlite3
import secrets
import traceback
from datetime import datetime

from aird.core.file_operations import remove_share_cloud_dir
from aird.sql_identifiers import format_update_by_id_sql

PRAGMA_TABLE_INFO = "PRAGMA table_info(shares)"


def _share_dict_full(row):
    """Build share dict from full row (id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date)."""
    (
        sid,
        created,
        paths_json,
        allowed_users_json,
        secret_token,
        share_type,
        allow_list_json,
        avoid_list_json,
        expiry_date,
    ) = row
    return {
        "id": sid,
        "created": created,
        "paths": json.loads(paths_json) if paths_json else [],
        "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
        "secret_token": secret_token,
        "share_type": share_type or "static",
        "allow_list": json.loads(allow_list_json) if allow_list_json else [],
        "avoid_list": json.loads(avoid_list_json) if avoid_list_json else [],
        "expiry_date": expiry_date,
    }


def _share_dict_legacy(row, with_secret: bool, with_share_type: bool):
    """Build share dict from legacy row (4–6 columns); defaults for allow_list, avoid_list, expiry_date."""
    sid, created, paths_json, allowed_users_json = row[0], row[1], row[2], row[3]
    secret_token = row[4] if with_secret else None
    share_type = (row[5] or "static") if with_share_type else "static"
    return {
        "id": sid,
        "created": created,
        "paths": json.loads(paths_json) if paths_json else [],
        "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
        "secret_token": secret_token,
        "share_type": share_type,
        "allow_list": [],
        "avoid_list": [],
        "expiry_date": None,
    }


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
) -> bool:
    """Insert a new share into the database."""
    try:
        with conn:
            conn.execute(
                "REPLACE INTO shares (id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )
        return True
    except Exception as e:
        logging.error(f"Failed to insert share {sid} into database: {e}")
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False


def delete_share(conn: sqlite3.Connection, sid: str) -> None:
    """Delete a share from the database."""
    try:
        with conn:
            conn.execute("DELETE FROM shares WHERE id = ?", (sid,))
        # Also remove cloud files directory if exists
        remove_share_cloud_dir(sid)
    except Exception:
        logging.debug("delete_share failed for %s", sid, exc_info=True)


def _build_legacy_updates(kwargs: dict):
    """Return (updates list, values list) for allowed_users/paths from kwargs."""
    updates = []
    values = []
    field_sql = {
        "allowed_users": "allowed_users = ?",
        "paths": "paths = ?",
    }
    for field, value in kwargs.items():
        if field not in field_sql:
            continue
        updates.append(field_sql[field])
        values.append(
            json.dumps(value)
            if field in ("allowed_users", "paths") and value is not None
            else value
        )
    return (updates, values)


def _build_token_update(disable_token: bool | None, secret_token: str | None):
    """Return (updates list, values list) for secret_token column update."""
    if disable_token is None and secret_token is None:
        return ([], [])
    updates = []
    values = []
    if disable_token is True:
        logging.debug("update_share - disable_token=True, setting secret_token to None")
        updates.append("secret_token = ?")
        values.append(None)
    else:
        new_token = secrets.token_urlsafe(64) if secret_token is None else secret_token
        updates.append("secret_token = ?")
        values.append(new_token)
    return (updates, values)


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
    """Update share information."""
    try:
        updates = []
        values = []

        if share_type is not None:
            updates.append("share_type = ?")
            values.append(share_type)

        tok_updates, tok_values = _build_token_update(disable_token, secret_token)
        updates.extend(tok_updates)
        values.extend(tok_values)

        if allow_list is not None:
            updates.append("allow_list = ?")
            values.append(json.dumps(allow_list) if allow_list else None)
        if avoid_list is not None:
            updates.append("avoid_list = ?")
            values.append(json.dumps(avoid_list) if avoid_list else None)
        if expiry_date is not None:
            updates.append("expiry_date = ?")
            values.append(expiry_date)

        leg_updates, leg_values = _build_legacy_updates(kwargs)
        updates.extend(leg_updates)
        values.extend(leg_values)

        if not updates:
            return False

        values.append(sid)
        with conn:
            cursor = conn.execute(
                format_update_by_id_sql("shares", ", ".join(updates)),
                values,
            )
            logging.debug("Update executed, rows affected: %s", cursor.rowcount)
        return True
    except Exception as e:
        logging.error("Failed to update share %s: %s", sid, e)
        logging.debug("Traceback: %s", traceback.format_exc())
        return False


def _get_share_row(conn: sqlite3.Connection, sid: str, columns: list) -> tuple | None:
    """Execute the appropriate SELECT for column set and return row or None."""
    full_set = {"secret_token", "share_type", "allow_list", "avoid_list", "expiry_date"}
    if full_set.issubset(set(columns)):
        cursor = conn.execute(
            "SELECT id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date FROM shares WHERE id = ?",
            (sid,),
        )
    elif "secret_token" in columns and "share_type" in columns:
        cursor = conn.execute(
            "SELECT id, created, paths, allowed_users, secret_token, share_type FROM shares WHERE id = ?",
            (sid,),
        )
    elif "secret_token" in columns:
        cursor = conn.execute(
            "SELECT id, created, paths, allowed_users, secret_token FROM shares WHERE id = ?",
            (sid,),
        )
    else:
        cursor = conn.execute(
            "SELECT id, created, paths, allowed_users FROM shares WHERE id = ?",
            (sid,),
        )
    return cursor.fetchone()


def _share_row_to_result(row: tuple) -> dict:
    """Build share dict from fetched row (full or legacy schema)."""
    n = len(row)
    if n >= 9:
        return _share_dict_full(row)
    return _share_dict_legacy(row, with_secret=(n > 4), with_share_type=(n > 5))


def get_share_by_id(conn: sqlite3.Connection, sid: str) -> dict | None:
    """Get a single share by ID from database."""
    try:
        logging.debug("get_share_by_id called with sid=%r", sid)
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        columns = [row[1] for row in cursor.fetchall()]
        row = _get_share_row(conn, sid, columns)
        if not row:
            logging.debug("No share found for sid=%r", sid)
            return None
        return _share_row_to_result(row)
    except Exception as e:
        logging.error("Error getting share %s: %s", sid, e)
        logging.debug("Traceback: %s", traceback.format_exc())
        return None


def _row_to_share_entry(row, has_secret: bool):
    """Convert a share row to dict entry (id key, rest as value)."""
    n = len(row)
    _, created, paths_json, allowed_users_json = row[0], row[1], row[2], row[3]
    secret_token = row[4] if has_secret and n > 4 else None
    return {
        "created": created,
        "paths": json.loads(paths_json) if paths_json else [],
        "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
        "secret_token": secret_token,
    }


def get_all_shares(conn: sqlite3.Connection) -> dict:
    """Get all shares from database."""
    try:
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        columns = [row[1] for row in cursor.fetchall()]
        has_secret = "secret_token" in columns
        if has_secret:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares"
            )
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares"
            )
        return {row[0]: _row_to_share_entry(row, has_secret) for row in cursor}
    except Exception as e:
        logging.error("Error getting all shares: %s", e)
        return {}


def get_shares_for_path(conn: sqlite3.Connection, file_path: str) -> list:
    """Get all shares that contain a specific file path."""
    try:
        cursor = conn.execute(PRAGMA_TABLE_INFO)
        columns = [row[1] for row in cursor.fetchall()]
        has_secret = "secret_token" in columns
        if has_secret:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares"
            )
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares"
            )
        matching_shares = []
        for row in cursor:
            paths = json.loads(row[2]) if row[2] else []
            if file_path not in paths:
                continue
            entry = _row_to_share_entry(row, has_secret)
            entry["id"] = row[0]
            entry["paths"] = paths
            matching_shares.append(entry)
        return matching_shares
    except Exception as e:
        logging.error("Error getting shares for path %s: %s", file_path, e)
        return []


def is_share_expired(expiry_date: str) -> bool:
    """Check if a share has expired based on expiry_date using system time."""
    if not expiry_date:
        return False

    try:
        # Parse the expiry date and convert to naive datetime (system time)
        expiry_datetime = datetime.fromisoformat(expiry_date.replace("Z", ""))

        # Get current system time (naive datetime)
        current_datetime = datetime.now()

        # Simple comparison using system time
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
