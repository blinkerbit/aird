"""User management database functions."""

import secrets
import json
import logging
import sqlite3
import hashlib
from datetime import datetime


# Import password hashing dependencies
try:
    from argon2 import PasswordHasher
    from argon2 import exceptions as argon2_exceptions
    ARGON2_AVAILABLE = True
    PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
except Exception:
    ARGON2_AVAILABLE = False
    PH = None


def hash_password(password: str) -> str:
    """Hash a password using Argon2. Falls back to legacy only if Argon2 unavailable."""
    if ARGON2_AVAILABLE and PH is not None:
        return PH.hash(password)
    # Legacy fallback: salted SHA-256
    salt = secrets.token_hex(32)
    pwd_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return f"{salt}:{pwd_hash}"


def verify_password(password: str, password_hash: str) -> bool:
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


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str = 'user') -> dict:
    """Create a new user in the database"""
    try:
        password_hash = hash_password(password)
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


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
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


def get_all_users(conn: sqlite3.Connection) -> list[dict]:
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


def search_users(conn: sqlite3.Connection, query: str) -> list[dict]:
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


def update_user(conn: sqlite3.Connection, user_id: int, **kwargs) -> bool:
    """Update user information"""
    try:
        valid_fields = ['username', 'password', 'password_hash', 'role', 'active', 'last_login']
        updates = []
        values = []
        
        for field, value in kwargs.items():
            if field in valid_fields:
                if field == 'password' and value:  # Special handling for password
                    updates.append('password_hash = ?')
                    values.append(hash_password(value))
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


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """Delete a user from the database"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0
    except Exception:
        return False


def authenticate_user(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    """Authenticate a user and return user data if successful"""
    user = get_user_by_username(conn, username)
    if not user or not user.get('active'):
        return None
    
    if verify_password(password, user['password_hash']):
        # Update last login time
        update_user(conn, user['id'], last_login=datetime.now().isoformat())
        return user
    
    return None


def assign_admin_privileges(conn: sqlite3.Connection, admin_users: list) -> None:
    """Assign admin role to specified users"""
    if not admin_users:
        return
    
    for username in admin_users:
        try:
            user = get_user_by_username(conn, username)
            if user and user['role'] != 'admin':
                update_user(conn, user['id'], role='admin')
                logging.info(f"Assigned admin privileges to user: {username}")
        except Exception as e:
            logging.error(f"Failed to assign admin privileges to {username}: {e}")
