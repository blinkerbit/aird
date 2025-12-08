"""Share management database functions."""

import json
import logging
import sqlite3
import secrets
from datetime import datetime

from aird.core.file_operations import (
    get_all_files_recursive,
    filter_files_by_patterns,
    remove_share_cloud_dir
)


def insert_share(conn: sqlite3.Connection, sid: str, created: str, paths: list[str], 
                allowed_users: list[str] = None, secret_token: str = None, 
                share_type: str = "static", allow_list: list[str] = None, 
                avoid_list: list[str] = None, expiry_date: str = None) -> bool:
    """Insert a new share into the database."""
    try:
        with conn:
            conn.execute(
                "REPLACE INTO shares (id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, created, json.dumps(paths), json.dumps(allowed_users) if allowed_users else None, 
                 secret_token, share_type, json.dumps(allow_list) if allow_list else None, 
                 json.dumps(avoid_list) if avoid_list else None, expiry_date),
            )
        return True
    except Exception as e:
        logging.error(f"Failed to insert share {sid} into database: {e}")
        import traceback
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
        pass


def update_share(conn: sqlite3.Connection, sid: str, share_type: str = None, 
                disable_token: bool = None, allow_list: list = None, 
                avoid_list: list = None, secret_token: str = None, 
                expiry_date: str = None, **kwargs) -> bool:
    """Update share information."""
    try:
        updates = []
        values = []

        if share_type is not None:
            updates.append("share_type = ?")
            values.append(share_type)
        
        token_update_requested = (disable_token is not None) or (secret_token is not None)
        if token_update_requested:
            if disable_token is True:
                logging.debug(f"update_share - disable_token={disable_token}")
                logging.debug("Disabling token (setting to None)")
                updates.append("secret_token = ?")
                values.append(None)
            else:
                new_token = secrets.token_urlsafe(64) if secret_token is None else secret_token
                updates.append("secret_token = ?")
                values.append(new_token)
        
        if allow_list is not None:
            updates.append("allow_list = ?")
            values.append(json.dumps(allow_list) if allow_list else None)
        
        if avoid_list is not None:
            updates.append("avoid_list = ?")
            values.append(json.dumps(avoid_list) if avoid_list else None)
        
        if expiry_date is not None:
            updates.append("expiry_date = ?")
            values.append(expiry_date)

        # Handle legacy parameters
        valid_fields = ['allowed_users', 'paths']
        for field, value in kwargs.items():
            if field in valid_fields:
                updates.append(f"{field} = ?")
                if field in ['allowed_users', 'paths'] and value is not None:
                    values.append(json.dumps(value))
                else:
                    values.append(value)

        if not updates:
            return False

        values.append(sid)
        query = f"UPDATE shares SET {', '.join(updates)} WHERE id = ?"

        with conn:
            cursor = conn.execute(query, values)
            logging.debug(f"Update executed, rows affected: {cursor.rowcount}")
            
        return True
    except Exception as e:
        logging.error(f"Failed to update share {sid}: {e}")
        import traceback
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False


def get_share_by_id(conn: sqlite3.Connection, sid: str) -> dict:
    """Get a single share by ID from database."""
    try:
        logging.debug(f"get_share_by_id called with sid='{sid}'")
        
        # Check if secret_token column exists
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'secret_token' in columns and 'share_type' in columns and 'allow_list' in columns and 'avoid_list' in columns and 'expiry_date' in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            if row:
                sid, created, paths_json, allowed_users_json, secret_token, share_type, allow_list_json, avoid_list_json, expiry_date = row
                return {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": secret_token,
                    "share_type": share_type or "static",
                    "allow_list": json.loads(allow_list_json) if allow_list_json else [],
                    "avoid_list": json.loads(avoid_list_json) if avoid_list_json else [],
                    "expiry_date": expiry_date
                }
        elif 'secret_token' in columns and 'share_type' in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token, share_type FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            if row:
                sid, created, paths_json, allowed_users_json, secret_token, share_type = row
                return {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": secret_token,
                    "share_type": share_type or "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None
                }
        elif 'secret_token' in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            if row:
                sid, created, paths_json, allowed_users_json, secret_token = row
                return {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": secret_token,
                    "share_type": "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None
                }
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            if row:
                sid, created, paths_json, allowed_users_json = row
                return {
                    "id": sid,
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": None,
                    "share_type": "static",
                    "allow_list": [],
                    "avoid_list": [],
                    "expiry_date": None
                }
        
        logging.debug(f"No share found for sid='{sid}'")
        return None
    except Exception as e:
        logging.error(f"Error getting share {sid}: {e}")
        import traceback
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return None


def get_all_shares(conn: sqlite3.Connection) -> dict:
    """Get all shares from database."""
    try:
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'secret_token' in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares"
            )
            shares = {}
            for row in cursor:
                sid, created, paths_json, allowed_users_json, secret_token = row
                shares[sid] = {
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": secret_token
                }
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares"
            )
            shares = {}
            for row in cursor:
                sid, created, paths_json, allowed_users_json = row
                shares[sid] = {
                    "created": created,
                    "paths": json.loads(paths_json) if paths_json else [],
                    "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                    "secret_token": None
                }
        return shares
    except Exception as e:
        print(f"Error getting all shares: {e}")
        return {}


def get_shares_for_path(conn: sqlite3.Connection, file_path: str) -> list:
    """Get all shares that contain a specific file path."""
    try:
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'secret_token' in columns:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares"
            )
            matching_shares = []
            for row in cursor:
                sid, created, paths_json, allowed_users_json, secret_token = row
                paths = json.loads(paths_json) if paths_json else []
                if file_path in paths:
                    matching_shares.append({
                        "id": sid,
                        "created": created,
                        "paths": paths,
                        "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                        "secret_token": secret_token
                    })
        else:
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares"
            )
            matching_shares = []
            for row in cursor:
                sid, created, paths_json, allowed_users_json = row
                paths = json.loads(paths_json) if paths_json else []
                if file_path in paths:
                    matching_shares.append({
                        "id": sid,
                        "created": created,
                        "paths": paths,
                        "allowed_users": json.loads(allowed_users_json) if allowed_users_json else None,
                        "secret_token": None
                    })
        return matching_shares
    except Exception as e:
        print(f"Error getting shares for path {file_path}: {e}")
        return []


def is_share_expired(expiry_date: str) -> bool:
    """Check if a share has expired based on expiry_date using system time."""
    if not expiry_date:
        return False
    
    try:
        # Parse the expiry date and convert to naive datetime (system time)
        expiry_datetime = datetime.fromisoformat(expiry_date.replace('Z', ''))
        
        # Get current system time (naive datetime)
        current_datetime = datetime.now()
        
        # Simple comparison using system time
        is_expired = current_datetime > expiry_datetime
        logging.debug(f"Checking expiry: current={current_datetime}, expiry={expiry_datetime}, expired={is_expired}")
        return is_expired
    except Exception as e:
        logging.error(f"Error checking expiry date {expiry_date}: {e}")
        return False


def cleanup_expired_shares(conn: sqlite3.Connection) -> int:
    """Remove expired shares from the database. Returns the number of shares deleted."""
    try:
        cursor = conn.execute("SELECT id, expiry_date FROM shares WHERE expiry_date IS NOT NULL")
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
