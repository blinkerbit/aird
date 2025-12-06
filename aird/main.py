import argparse
import asyncio
from collections import deque
import concurrent.futures
from datetime import datetime
import glob
import gzip
import hashlib
from io import BytesIO
import json
import logging
import mimetypes
import mmap
import os
import pathlib
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
from typing import Set
from urllib.parse import unquote, urlparse
import weakref

import aiofiles
from ldap3 import ALL, Connection, Server
import tornado.escape as tornado_escape
import tornado.ioloop
import tornado.web
import tornado.websocket

import aird.constants as constants
import aird.config as config

# Set up module logger
logger = logging.getLogger(__name__)

from aird.handlers.admin_handlers import (
    AdminHandler,
    AdminUsersHandler,
    LDAPConfigCreateHandler,
    LDAPConfigDeleteHandler,
    LDAPConfigEditHandler,
    LDAPConfigHandler,
    LDAPSyncHandler,
    UserCreateHandler,
    UserDeleteHandler,
    UserEditHandler,
    WebSocketStatsHandler,
)
from aird.handlers.api_handlers import (
    FeatureFlagSocketHandler,
    FileListAPIHandler,
    FileStreamHandler,
    ShareDetailsAPIHandler,
    ShareDetailsByIdAPIHandler,
    ShareListAPIHandler,
    SuperSearchHandler,
    SuperSearchWebSocketHandler,
    UserSearchAPIHandler,
)
from aird.handlers.auth_handlers import (
    AdminLoginHandler,
    LDAPLoginHandler,
    LoginHandler,
    LogoutHandler,
    ProfileHandler,
)
from aird.handlers.base_handler import BaseHandler
from aird.handlers.file_op_handlers import (
    CloudUploadHandler,
    DeleteHandler,
    EditHandler,
    RenameHandler,
    UploadHandler,
)
from aird.handlers.share_handlers import (
    ShareCreateHandler,
    ShareFilesHandler,
    ShareRevokeHandler,
    ShareUpdateHandler,
    SharedFileHandler,
    SharedListHandler,
    TokenVerificationHandler,
)
from aird.handlers.view_handlers import (
    CloudDownloadHandler,
    CloudFilesHandler,
    CloudProvidersHandler,
    MainHandler,
    RootHandler,
    EditViewHandler,
)
from aird.utils.util import *
from aird.cloud import (
    CloudManager,
    CloudProviderError,
    GoogleDriveProvider,
    OneDriveProvider,
)
# Secure password hashing (Priority 1)
try:
    from argon2 import PasswordHasher
    from argon2 import exceptions as argon2_exceptions
    ARGON2_AVAILABLE = True
    PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
except Exception:
    ARGON2_AVAILABLE = False
    PH = None

RUST_AVAILABLE = False
HybridFileHandler = None
HybridCompressionHandler = None

# ------------------------
# SQLite persistence layer
# ------------------------

def _get_data_dir() -> str:
    """Return the data directory, creating it if it doesn't exist."""
    try:
        if os.name == 'nt':  # Windows
            base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or os.path.expanduser('~\\AppData\\Local')
        elif sys.platform == 'darwin':  # macOS
            base = os.path.expanduser('~/Library/Application Support')
        else:  # Linux and others
            base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
        data_dir = os.path.join(base, 'aird')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    except Exception:
        # Fallback to current directory
        return os.getcwd()

def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_flags (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shares (
            id TEXT PRIMARY KEY,
            created TEXT NOT NULL,
            paths TEXT NOT NULL,
            allowed_users TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_login TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ldap_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            server TEXT NOT NULL,
            ldap_base_dn TEXT NOT NULL,
            ldap_member_attributes TEXT NOT NULL DEFAULT 'member',
            user_template TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ldap_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER NOT NULL,
            sync_type TEXT NOT NULL,
            users_found INTEGER NOT NULL,
            users_created INTEGER NOT NULL,
            users_removed INTEGER NOT NULL,
            sync_time TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT,
            FOREIGN KEY (config_id) REFERENCES ldap_configs (id)
        )
        """
    )

    # Migration for shares table
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(shares)")
    columns = [column[1] for column in cursor.fetchall()]
    if "allowed_users" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN allowed_users TEXT")
    if "secret_token" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN secret_token TEXT")
    if "share_type" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN share_type TEXT DEFAULT 'static'")
    if "allow_list" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN allow_list TEXT")
    if "avoid_list" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN avoid_list TEXT")
    if "expiry_date" not in columns:
        cursor.execute("ALTER TABLE shares ADD COLUMN expiry_date TEXT")

    conn.commit()

def _load_feature_flags(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute("SELECT key, value FROM feature_flags").fetchall()
        return {k: bool(v) for (k, v) in rows}
    except Exception:
        return {}

def _save_feature_flags(conn: sqlite3.Connection, flags: dict) -> None:
    try:
        with conn:
            for k, v in flags.items():
                conn.execute(
                    "REPLACE INTO feature_flags (key, value) VALUES (?, ?)",
                    (k, 1 if v else 0),
                )
    except Exception:
        pass


def _insert_share(conn: sqlite3.Connection, sid: str, created: str, paths: list[str], allowed_users: list[str] = None, secret_token: str = None, share_type: str = "static", allow_list: list[str] = None, avoid_list: list[str] = None, expiry_date: str = None) -> bool:
    try:
        with conn:
            conn.execute(
                "REPLACE INTO shares (id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, created, json.dumps(paths), json.dumps(allowed_users) if allowed_users else None, secret_token, share_type, json.dumps(allow_list) if allow_list else None, json.dumps(avoid_list) if avoid_list else None, expiry_date),
            )
        return True
    except Exception as e:
        logging.error(f"Failed to insert share {sid} into database: {e}")
        import traceback
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return False

def _delete_share(conn: sqlite3.Connection, sid: str) -> None:
    try:
        with conn:
            conn.execute("DELETE FROM shares WHERE id = ?", (sid,))
    except Exception:
        pass

def _update_share(conn: sqlite3.Connection, sid: str, share_type: str = None, disable_token: bool = None, allow_list: list = None, avoid_list: list = None, secret_token: str = None, expiry_date: str = None, **kwargs) -> bool:
    """Update share information"""
    try:
        updates = []
        values = []

        # Handle new parameters
        if share_type is not None:
            updates.append("share_type = ?")
            values.append(share_type)
        
        token_update_requested = (disable_token is not None) or (secret_token is not None)
        if token_update_requested:
            if disable_token is True:
                logging.debug(f"_update_share - disable_token={disable_token}")
                logging.debug("Disabling token (setting to None)")
                updates.append("secret_token = ?")
                values.append(None)
            else:
                new_token = secrets.token_urlsafe(32) if secret_token is None else secret_token
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

def _is_share_expired(expiry_date: str) -> bool:
    """Check if a share has expired based on expiry_date using system time"""
    if not expiry_date:
        return False
    
    try:
        from datetime import datetime
        
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

def _cleanup_expired_shares(conn: sqlite3.Connection) -> int:
    """Remove expired shares from the database. Returns the number of shares deleted."""
    try:
        from datetime import datetime
        cursor = conn.execute("SELECT id, expiry_date FROM shares WHERE expiry_date IS NOT NULL")
        rows = cursor.fetchall()
        
        deleted_count = 0
        for share_id, expiry_date in rows:
            if _is_share_expired(expiry_date):
                with conn:
                    conn.execute("DELETE FROM shares WHERE id = ?", (share_id,))
                    deleted_count += 1
                    logging.info(f"Deleted expired share: {share_id}")
        
        return deleted_count
    except Exception as e:
        logging.error(f"Error cleaning up expired shares: {e}")
        return 0

def _get_share_by_id(conn: sqlite3.Connection, sid: str) -> dict:
    """Get a single share by ID from database"""
    try:
        logging.debug(f"_get_share_by_id called with sid='{sid}'")
        logging.debug(f"conn is {'None' if conn is None else 'available'}")
        
        # Check if secret_token column exists
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        logging.debug(f"Table columns: {columns}")
        
        if 'secret_token' in columns and 'share_type' in columns and 'allow_list' in columns and 'avoid_list' in columns and 'expiry_date' in columns:
            logging.debug(f"Using query with all columns including expiry_date")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json, secret_token, share_type, allow_list_json, avoid_list_json, expiry_date = row
                result = {
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
                logging.debug(f"Returning share data: {result}")
                return result
        elif 'secret_token' in columns and 'share_type' in columns:
            logging.debug(f"Using query with secret_token and share_type columns")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token, share_type FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json, secret_token, share_type = row
                result = {
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
                logging.debug(f"Returning share data: {result}")
                return result
        elif 'secret_token' in columns:
            logging.debug(f"Using query with secret_token column")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users, secret_token FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json, secret_token = row
                result = {
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
                logging.debug(f"Returning share data: {result}")
                return result
        else:
            logging.debug(f"Using query without secret_token column")
            cursor = conn.execute(
                "SELECT id, created, paths, allowed_users FROM shares WHERE id = ?",
                (sid,)
            )
            row = cursor.fetchone()
            logging.debug(f"Query result: {row}")
            if row:
                sid, created, paths_json, allowed_users_json = row
                result = {
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
                logging.debug(f"Returning share data: {result}")
                return result
        
        logging.debug(f"No share found for sid='{sid}'")
        return None
    except Exception as e:
        logging.error(f"Error getting share {sid}: {e}")
        import traceback
        logging.debug(f"Traceback: {traceback.format_exc()}")
        return None

def _get_all_shares(conn: sqlite3.Connection) -> dict:
    """Get all shares from database"""
    try:
        # Check if secret_token column exists
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
        logger.error(f"Error getting all shares: {e}")
        return {}

def _get_shares_for_path(conn: sqlite3.Connection, file_path: str) -> list:
    """Get all shares that contain a specific file path"""
    try:
        # Check if secret_token column exists
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
        logger.error(f"Error getting shares for path {file_path}: {e}")
        return []

def _load_websocket_config(conn: sqlite3.Connection) -> dict:
    """Load WebSocket configuration from SQLite database."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)")
        rows = conn.execute("SELECT key, value FROM websocket_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}

def _save_websocket_config(conn: sqlite3.Connection, config: dict) -> None:
    """Save WebSocket configuration to SQLite database."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)")
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO websocket_config (key, value) VALUES (?, ?)",
                    (key, int(value)),
                )
    except Exception:
        pass

# ------------------------
# User management functions
# ------------------------

def _hash_password(password: str) -> str:
    """Hash a password using Argon2 (Priority 1). Falls back to legacy only if Argon2 unavailable."""
    if ARGON2_AVAILABLE and PH is not None:
        return PH.hash(password)
    # Legacy fallback (not recommended): salted SHA-256
    salt = secrets.token_hex(32)
    pwd_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return f"{salt}:{pwd_hash}"

def _verify_password(password: str, password_hash: str) -> bool:
    """Verify password supporting Argon2 and legacy salted SHA-256."""
    # Try Argon2 first
    if password_hash and password_hash.startswith("$argon2") and ARGON2_AVAILABLE and PH is not None:
        try:
            return PH.verify(password_hash, password)
        except argon2_exceptions.VerifyMismatchError:
            return False
        except Exception:
            return False
    # Legacy format: salt:hash
    try:
        salt, stored_hash = password_hash.split(':', 1)
        pwd_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
        return pwd_hash == stored_hash
    except Exception:
        return False

def _create_user(conn: sqlite3.Connection, username: str, password: str, role: str = 'user') -> dict:
    """Create a new user in the database"""
    try:
        password_hash = _hash_password(password)
        created_at = datetime.now().isoformat()
        
        with conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, created_at)
            )
            user_id = cursor.lastrowid
            
        return {
            "id": user_id,
            "username": username,
            "role": role,
            "created_at": created_at,
            "active": True,
            "last_login": None
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{username}' already exists")
    except Exception as e:
        raise Exception(f"Failed to create user: {str(e)}")

def _get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    """Get user by username"""
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at, active, last_login FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        
        if row:
            return {
                "id": row[0],
                "username": row[1],
                "password_hash": row[2],
                "role": row[3],
                "created_at": row[4],
                "active": bool(row[5]),
                "last_login": row[6]
            }
        return None
    except Exception:
        return None

def _get_all_users(conn: sqlite3.Connection) -> list[dict]:
    """Get all users from the database"""
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, active, last_login FROM users ORDER BY created_at DESC"
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5]
            }
            for row in rows
        ]
    except Exception:
        return []

def _search_users(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Search users by username (case-insensitive)"""
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, active, last_login FROM users WHERE username LIKE ? AND active = 1 ORDER BY username LIMIT 20",
            (f"%{query}%",)
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "username": row[1],
                "role": row[2],
                "created_at": row[3],
                "active": bool(row[4]),
                "last_login": row[5]
            }
            for row in rows
        ]
    except Exception:
        return []

def _update_user(conn: sqlite3.Connection, user_id: int, **kwargs) -> bool:
    """Update user information"""
    try:
        valid_fields = ['username', 'password_hash', 'role', 'active', 'last_login']
        updates = []
        values = []
        
        for field, value in kwargs.items():
            if field in valid_fields:
                if field == 'password' and value:  # Special handling for password
                    updates.append('password_hash = ?')
                    values.append(_hash_password(value))
                elif field == 'active':
                    updates.append('active = ?')
                    values.append(1 if value else 0)
                else:
                    updates.append(f'{field} = ?')
                    values.append(value)
        
        if not updates:
            return False
            
        values.append(user_id)
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
        
        with conn:
            conn.execute(query, values)
        return True
    except Exception:
        return False

def _create_ldap_config(conn: sqlite3.Connection, name: str, server: str, ldap_base_dn: str, 
                       ldap_member_attributes: str, user_template: str) -> dict:
    """Create a new LDAP configuration"""
    try:
        created_at = datetime.now().isoformat()
        
        with conn:
            cursor = conn.execute(
                """INSERT INTO ldap_configs (name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at)
            )
            config_id = cursor.lastrowid
            
        return {
            "id": config_id,
            "name": name,
            "server": server,
            "ldap_base_dn": ldap_base_dn,
            "ldap_member_attributes": ldap_member_attributes,
            "user_template": user_template,
            "created_at": created_at,
            "active": True
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"LDAP configuration '{name}' already exists")
    except Exception as e:
        raise Exception(f"Failed to create LDAP configuration: {str(e)}")

def _get_all_ldap_configs(conn: sqlite3.Connection) -> list[dict]:
    """Get all LDAP configurations"""
    try:
        rows = conn.execute(
            "SELECT id, name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at, active FROM ldap_configs ORDER BY created_at DESC"
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "name": row[1],
                "server": row[2],
                "ldap_base_dn": row[3],
                "ldap_member_attributes": row[4],
                "user_template": row[5],
                "created_at": row[6],
                "active": bool(row[7])
            }
            for row in rows
        ]
    except Exception:
        return []

def _get_ldap_config_by_id(conn: sqlite3.Connection, config_id: int) -> dict | None:
    """Get LDAP configuration by ID"""
    try:
        row = conn.execute(
            "SELECT id, name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at, active FROM ldap_configs WHERE id = ?",
            (config_id,)
        ).fetchone()
        
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "server": row[2],
                "ldap_base_dn": row[3],
                "ldap_member_attributes": row[4],
                "user_template": row[5],
                "created_at": row[6],
                "active": bool(row[7])
            }
    except Exception:
        return None

def _update_ldap_config(conn: sqlite3.Connection, config_id: int, **kwargs) -> bool:
    """Update LDAP configuration"""
    try:
        valid_fields = ['name', 'server', 'ldap_base_dn', 'ldap_member_attributes', 'user_template', 'active']
        updates = []
        values = []
        
        for field, value in kwargs.items():
            if field in valid_fields:
                if field == 'active':
                    updates.append('active = ?')
                    values.append(1 if value else 0)
                else:
                    updates.append(f'{field} = ?')
                    values.append(value)
        
        if not updates:
            return False
            
        values.append(config_id)
        query = f"UPDATE ldap_configs SET {', '.join(updates)} WHERE id = ?"
        
        with conn:
            conn.execute(query, values)
        return True
    except Exception:
        return False

def _delete_ldap_config(conn: sqlite3.Connection, config_id: int) -> bool:
    """Delete LDAP configuration"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM ldap_configs WHERE id = ?", (config_id,))
            return cursor.rowcount > 0
    except Exception:
        return False

def _log_ldap_sync(conn: sqlite3.Connection, config_id: int, sync_type: str, users_found: int, 
                  users_created: int, users_removed: int, status: str, error_message: str = None) -> None:
    """Log LDAP synchronization results"""
    try:
        sync_time = datetime.now().isoformat()
        with conn:
            conn.execute(
                """INSERT INTO ldap_sync_log (config_id, sync_type, users_found, users_created, users_removed, sync_time, status, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (config_id, sync_type, users_found, users_created, users_removed, sync_time, status, error_message)
            )
    except Exception:
        pass  # Don't fail the sync if logging fails

def _get_ldap_sync_logs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Get recent LDAP sync logs"""
    try:
        rows = conn.execute(
            """SELECT l.id, l.config_id, c.name, l.sync_type, l.users_found, l.users_created, l.users_removed, 
                      l.sync_time, l.status, l.error_message
               FROM ldap_sync_log l
               JOIN ldap_configs c ON l.config_id = c.id
               ORDER BY l.sync_time DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        
        return [
            {
                "id": row[0],
                "config_id": row[1],
                "config_name": row[2],
                "sync_type": row[3],
                "users_found": row[4],
                "users_created": row[5],
                "users_removed": row[6],
                "sync_time": row[7],
                "status": row[8],
                "error_message": row[9]
            }
            for row in rows
        ]
    except Exception:
        return []

def _sync_ldap_users(conn: sqlite3.Connection) -> dict:
    """Synchronize users from all active LDAP configurations"""
    if not conn:
        return {"status": "error", "message": "Database not available"}
    
    try:
        # Get all active LDAP configurations
        configs = _get_all_ldap_configs(conn)
        active_configs = [c for c in configs if c['active']]
        
        if not active_configs:
            return {"status": "success", "message": "No active LDAP configurations found"}
        
        all_ldap_users = set()  # Use set to automatically handle duplicates
        sync_results = []
        
        for config in active_configs:
            try:
                # Connect to LDAP server
                from ldap3 import Server, Connection, ALL
                server = Server(config['server'], get_info=ALL)
                conn_ldap = Connection(server)
                
                if not conn_ldap.bind():
                    sync_results.append({
                        "config_name": config['name'],
                        "status": "error",
                        "message": f"Failed to bind to LDAP server: {config['server']}"
                    })
                    continue
                
                # Search for groups and their members
                search_filter = f"(objectClass=groupOfNames)"
                conn_ldap.search(
                    search_base=config['ldap_base_dn'],
                    search_filter=search_filter,
                    attributes=[config['ldap_member_attributes']]
                )
                
                config_users = set()
                for entry in conn_ldap.entries:
                    if hasattr(entry, config['ldap_member_attributes']):
                        members = getattr(entry, config['ldap_member_attributes'])
                        if members:
                            for member in members:
                                # Extract username using the user template
                                username = _extract_username_from_dn(member, config['user_template'])
                                if username:
                                    config_users.add(username)
                                    all_ldap_users.add(username)
                
                sync_results.append({
                    "config_name": config['name'],
                    "status": "success",
                    "users_found": len(config_users)
                })
                
                # Log the sync for this config
                _log_ldap_sync(conn, config['id'], 'group_sync', len(config_users), 0, 0, 'success')
                
            except Exception as e:
                sync_results.append({
                    "config_name": config['name'],
                    "status": "error",
                    "message": str(e)
                })
                _log_ldap_sync(conn, config['id'], 'group_sync', 0, 0, 0, 'error', str(e))
        
        # Now sync users with the database
        users_created = 0
        users_removed = 0
        
        # Get current users from database
        current_users = _get_all_users(conn)
        current_usernames = {user['username'] for user in current_users}
        
        # Create new users that are in LDAP but not in database
        for username in all_ldap_users:
            if username not in current_usernames:
                try:
                    # Create user with a dummy password (they'll authenticate via LDAP)
                    _create_user(conn, username, "ldap_user", role='user')
                    users_created += 1
                except Exception as e:
                    logger.error(f"Failed to create user {username}: {e}")
        
        # Remove users that are in database but not in LDAP
        for user in current_users:
            if user['username'] not in all_ldap_users and user['username'] != 'admin':
                try:
                    _delete_user(conn, user['id'])
                    users_removed += 1
                except Exception as e:
                    logger.error(f"Failed to remove user {user['username']}: {e}")
        
        return {
            "status": "success",
            "total_ldap_users": len(all_ldap_users),
            "users_created": users_created,
            "users_removed": users_removed,
            "config_results": sync_results
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _extract_username_from_dn(dn: str, user_template: str) -> str:
    """Extract username from LDAP DN using the user template"""
    try:
        # Simple template matching - can be enhanced for more complex patterns
        if '{username}' in user_template:
            # For templates like "uid={username},ou=users,dc=example,dc=com"
            # We need to extract the actual username from the DN
            # This is a simplified implementation
            parts = dn.split(',')
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    # Case-insensitive comparison for LDAP attributes
                    key_lower = key.strip().lower()
                    if key_lower in ['uid', 'cn', 'samaccountname']:
                        return value.strip()
        return None
    except Exception:
        return None

def _start_ldap_sync_scheduler(conn: sqlite3.Connection) -> None:
    """Start the daily LDAP sync scheduler in a background thread"""
    def sync_worker():
        while True:
            try:
                # Run sync at 2 AM every day
                current_time = datetime.now()
                if current_time.hour == 2 and current_time.minute == 0:
                    logger.info("Starting daily LDAP sync...")
                    sync_result = _sync_ldap_users(conn)
                    if sync_result["status"] == "success":
                        logger.info(f"Daily LDAP sync completed: {sync_result.get('total_ldap_users', 0)} users found, {sync_result.get('users_created', 0)} created, {sync_result.get('users_removed', 0)} removed")
                    else:
                        logger.error(f"Daily LDAP sync failed: {sync_result.get('message', 'Unknown error')}")
                
                # Sleep for 1 minute to avoid busy waiting
                time.sleep(60)
            except Exception as e:
                logger.error(f"Error in LDAP sync scheduler: {e}")
                time.sleep(300)  # Sleep for 5 minutes on error
    
    if conn:
        sync_thread = threading.Thread(target=sync_worker, daemon=True)
        sync_thread.start()
        logger.info("LDAP sync scheduler started (daily at 2 AM)")

def _assign_admin_privileges(conn: sqlite3.Connection, admin_users: list) -> None:
    """Assign admin privileges to users listed in admin_users configuration"""
    if not admin_users or not conn:
        return
    
    try:
        for username in admin_users:
            if not username or not isinstance(username, str):
                continue
                
            # Check if user exists
            user = _get_user_by_username(conn, username)
            if user:
                # Update user role to admin if not already admin
                if user['role'] != 'admin':
                    _update_user(conn, user['id'], role='admin')
                    logger.info(f"Assigned admin privileges to existing user '{username}'")
            else:
                logger.info(f"User '{username}' not found in database - will be assigned admin privileges on first login")
    except Exception as e:
        logger.warning(f"Failed to assign admin privileges: {e}")

def _delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """Delete a user from the database"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0
    except Exception:
        return False

def _authenticate_user(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    """Authenticate a user and update last_login"""
    user = _get_user_by_username(conn, username)
    if user and user['active'] and _verify_password(password, user['password_hash']):
        # Update last login timestamp
        _update_user(conn, user['id'], last_login=datetime.now().isoformat())
        # Remove sensitive information before returning
        del user['password_hash']
        return user
    return None

# Import handlers from modules


def make_app(settings, ldap_enabled=False, ldap_server=None, ldap_base_dn=None, ldap_user_template=None, ldap_filter_template=None, ldap_attributes=None, ldap_attribute_map=None, admin_users=None):
    settings["template_path"] = os.path.join(os.path.dirname(__file__), "templates")
    # Limit request size to avoid Tornado rejecting large uploads with
    # "Content-Length too long" before our handler can respond.
    settings.setdefault("max_body_size", constants.MAX_FILE_SIZE)
    settings.setdefault("max_buffer_size", constants.MAX_FILE_SIZE)
    
    if ldap_enabled:
        settings["ldap_server"] = ldap_server
        settings["ldap_base_dn"] = ldap_base_dn
        settings["ldap_user_template"] = ldap_user_template
        settings["ldap_filter_template"] = ldap_filter_template
        settings["ldap_attributes"] = ldap_attributes
        settings["ldap_attribute_map"] = ldap_attribute_map
    
    # Add admin users configuration to settings
    if admin_users:
        settings["admin_users"] = admin_users
    
    if ldap_enabled:
        login_handler = LDAPLoginHandler
    else:
        login_handler = LoginHandler

    # Build routes list
    routes = [
        (r"/", RootHandler),
        (r"/login", login_handler),
        (r"/logout", LogoutHandler),
        (r"/profile", ProfileHandler),
        (r"/admin/login", AdminLoginHandler),
        (r"/admin", AdminHandler),
        (r"/admin/users", AdminUsersHandler),
        (r"/admin/users/create", UserCreateHandler),
        (r"/admin/users/edit/([0-9]+)", UserEditHandler),
        (r"/admin/users/delete", UserDeleteHandler),
        (r"/admin/websocket-stats", WebSocketStatsHandler),
        (r"/stream/(.*)", FileStreamHandler),
        (r"/features", FeatureFlagSocketHandler),
        (r"/upload", UploadHandler),
        (r"/delete", DeleteHandler),
        (r"/rename", RenameHandler),
        (r"/edit/(.*)", EditViewHandler),
        (r"/edit", EditHandler),
        (r"/api/files/(.*)", FileListAPIHandler),
        (r"/api/users/search", UserSearchAPIHandler),
        (r"/api/cloud/providers", CloudProvidersHandler),
        (r"/api/cloud/([a-z0-9_\-]+)/files", CloudFilesHandler),
        (r"/api/cloud/([a-z0-9_\-]+)/download", CloudDownloadHandler),
        (r"/api/cloud/([a-z0-9_\-]+)/upload", CloudUploadHandler),
        (r"/api/share/details", ShareDetailsAPIHandler),
        (r"/api/share/details_by_id", ShareDetailsByIdAPIHandler),
        (r"/share", ShareFilesHandler),
        (r"/share/create", ShareCreateHandler),
        (r"/share/revoke", ShareRevokeHandler),
        (r"/share/list", ShareListAPIHandler),
        (r"/share/update", ShareUpdateHandler),
        (r"/shared/([A-Za-z0-9_\-]+)/verify", TokenVerificationHandler),
        (r"/shared/([A-Za-z0-9_\-]+)", SharedListHandler),
        (r"/shared/([A-Za-z0-9_\-]+)/file/(.*)", SharedFileHandler),
        (r"/search", SuperSearchHandler),
        (r"/search/ws", SuperSearchWebSocketHandler),
        (r"/files/(.*)", MainHandler),
    ]
    
    # Add LDAP routes only if LDAP is enabled
    if ldap_enabled:
        routes.extend([
            (r"/admin/ldap", LDAPConfigHandler),
            (r"/admin/ldap/create", LDAPConfigCreateHandler),
            (r"/admin/ldap/edit/([0-9]+)", LDAPConfigEditHandler),
            (r"/admin/ldap/delete", LDAPConfigDeleteHandler),
            (r"/admin/ldap/sync", LDAPSyncHandler),
        ])
    
    return tornado.web.Application(routes, **settings)


def print_banner():
    """Log ASCII art banner for aird"""
    banner = """
 █████╗ ██╗██████╗ ██████╗ 
██╔══██╗██║██╔══██╗██╔══██╗
███████║██║██████╔╝██║  ██║
██╔══██║██║██╔══██╗██║  ██║
██║  ██║██║██║  ██║██████╔╝
╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚═════╝ 
"""
    print(banner)

def main():
    print_banner()
    
    config.init_config()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

    if config.LDAP_ENABLED:
        if not config.LDAP_SERVER:
            logger.error("LDAP is enabled, but --ldap-server is not configured.")
            return
        if not config.LDAP_BASE_DN:
            logger.error("LDAP is enabled, but --ldap-base-dn is not configured.")
            return
        if not config.LDAP_USER_TEMPLATE:
            logger.error("LDAP is enabled, but --ldap-user-template is not configured.")
            return
        if not config.LDAP_FILTER_TEMPLATE:
            logger.error("LDAP is enabled, but --ldap-filter-template is not configured.")
            return
        if not config.LDAP_ATTRIBUTES:
            logger.error("LDAP is enabled, but --ldap-attributes is not configured.")
            return

    # SSL validation
    if config.SSL_CERT and not config.SSL_KEY:
        logger.error("SSL certificate provided but SSL key is missing. Both --ssl-cert and --ssl-key are required for SSL.")
        return
    if config.SSL_KEY and not config.SSL_CERT:
        logger.error("SSL key provided but SSL certificate is missing. Both --ssl-cert and --ssl-key are required for SSL.")
        return
    if config.SSL_CERT and config.SSL_KEY:
        # Validate that certificate and key files exist
        if not os.path.exists(config.SSL_CERT):
            logger.error(f"SSL certificate file not found: {config.SSL_CERT}")
            return
        if not os.path.exists(config.SSL_KEY):
            logger.error(f"SSL key file not found: {config.SSL_KEY}")
            return

    constants.ACCESS_TOKEN = config.ACCESS_TOKEN
    constants.ADMIN_TOKEN = config.ADMIN_TOKEN
    constants.ROOT_DIR = os.path.abspath(config.ROOT_DIR)

    # Generate separate cookie secret for better security
    cookie_secret = secrets.token_urlsafe(64)
    
    settings = {
        "cookie_secret": cookie_secret,
        "xsrf_cookies": True,  # Enable CSRF protection
        "login_url": "/login",
        "admin_login_url": "/admin/login",
        "cloud_manager": constants.CLOUD_MANAGER,
    }

    # Initialize SQLite persistence under OS data dir
    try:
        data_dir = _get_data_dir()
        constants.DB_PATH = os.path.join(data_dir, 'aird.sqlite3')
        db_exists = os.path.exists(constants.DB_PATH)
        logger.info(f"SQLite database path: {constants.DB_PATH}")
        logger.info(f"Database already exists: {'Yes' if db_exists else 'No (will be created)'}")
        constants.DB_CONN = sqlite3.connect(constants.DB_PATH, check_same_thread=False)
        _init_db(constants.DB_CONN)
        # Load persisted feature flags and merge
        persisted_flags = _load_feature_flags(constants.DB_CONN)
        if persisted_flags:
            for k, v in persisted_flags.items():
                constants.FEATURE_FLAGS[k] = bool(v)
                logger.debug(f"Feature flag '{k}' set to {bool(v)} from database")
        
        # Log final feature flags status
        logger.info("Final feature flags:")
        for k, v in constants.FEATURE_FLAGS.items():
            logger.info(f"  {k}: {v}")
        
        # Start LDAP sync scheduler
        _start_ldap_sync_scheduler(constants.DB_CONN)
        # Database-only persistence for shares
        logger.info("Shares are now persisted directly in database")
        
        # Assign admin privileges to configured admin users
        _assign_admin_privileges(constants.DB_CONN, config.ADMIN_USERS)

        # Ensure database connection is working
        if constants.DB_CONN is None:
            logger.warning("Database connection is None, attempting to create...")
            try:
                constants.DB_PATH = os.path.join(os.path.expanduser('~'), '.local', 'aird', 'aird.sqlite3')
                os.makedirs(os.path.dirname(constants.DB_PATH), exist_ok=True)
                constants.DB_CONN = sqlite3.connect(constants.DB_PATH, check_same_thread=False)
                _init_db(constants.DB_CONN)
                logger.info(f"Created emergency database connection: {constants.DB_CONN}")
            except Exception as db_error:
                logger.error(f"Failed to create emergency database connection: {db_error}")

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        constants.DB_CONN = None
        logger.warning(f"DB_CONN set to None")

    app = make_app(settings, config.LDAP_ENABLED, config.LDAP_SERVER, config.LDAP_BASE_DN, config.LDAP_USER_TEMPLATE, config.LDAP_FILTER_TEMPLATE, config.LDAP_ATTRIBUTES, config.LDAP_ATTRIBUTE_MAP, config.ADMIN_USERS)
    
    # Configure SSL if certificates are provided
    ssl_options = None
    if config.SSL_CERT and config.SSL_KEY:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(config.SSL_CERT, config.SSL_KEY)
        ssl_options = ssl_context
    
    port = config.PORT
    while True:
        try:
            if ssl_options:
                app.listen(
                    port,
                    ssl_options=ssl_options,
                    max_body_size=constants.MAX_FILE_SIZE,
                    max_buffer_size=constants.MAX_FILE_SIZE,
                )
                logger.info(f"Serving HTTPS on 0.0.0.0 port {port} (https://0.0.0.0:{port}/) ...")
                print(f"https://{config.HOSTNAME}:{port}/")
            else:
                app.listen(
                    port,
                    max_body_size=constants.MAX_FILE_SIZE,
                    max_buffer_size=constants.MAX_FILE_SIZE,
                )
                logger.info(f"Serving HTTP on 0.0.0.0 port {port} (http://0.0.0.0:{port}/) ...")
                print(f"http://{config.HOSTNAME}:{port}/")

            # Setup periodic cleanup of expired shares
            def cleanup_expired_shares_periodic():
                """Periodic task to cleanup expired shares"""
                if constants.DB_CONN:
                    deleted = _cleanup_expired_shares(constants.DB_CONN)
                    if deleted > 0:
                        logger.info(f"Cleaned up {deleted} expired share(s)")
                # Schedule next cleanup in 1 hour (3600 seconds)
                tornado.ioloop.IOLoop.current().call_later(3600, cleanup_expired_shares_periodic)
            
            # Start cleanup in 1 hour
            tornado.ioloop.IOLoop.current().call_later(3600, cleanup_expired_shares_periodic)
            
            tornado.ioloop.IOLoop.current().start()
            break
        except OSError:
            port += 1
    
if __name__ == "__main__":
    main()
