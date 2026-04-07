"""Network share database operations."""

import logging
import sqlite3
from datetime import datetime

from aird.sql_identifiers import format_update_by_id_sql

logger = logging.getLogger(__name__)


def create_network_share(
    conn: sqlite3.Connection,
    share_id: str,
    name: str,
    folder_path: str,
    protocol: str,
    port: int,
    username: str,
    password: str,
    read_only: bool = False,
) -> bool:
    try:
        created_at = datetime.now().isoformat()
        with conn:
            conn.execute(
                """INSERT INTO network_shares
                   (id, name, folder_path, protocol, port, username, password, enabled, read_only, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    share_id,
                    name,
                    folder_path,
                    protocol,
                    port,
                    username,
                    password,
                    1 if read_only else 0,
                    created_at,
                ),
            )
        return True
    except Exception as e:
        logging.error("Failed to create network share %s: %s", share_id, e)
        return False


def get_all_network_shares(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT id, name, folder_path, protocol, port, username, password, enabled, read_only, created_at "
            "FROM network_shares ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "folder_path": r[2],
                "protocol": r[3],
                "port": r[4],
                "username": r[5],
                "password": r[6],
                "enabled": bool(r[7]),
                "read_only": bool(r[8]),
                "created_at": r[9],
            }
            for r in rows
        ]
    except Exception as e:
        logging.error("Failed to load network shares: %s", e)
        return []


def get_network_share(conn: sqlite3.Connection, share_id: str) -> dict | None:
    try:
        row = conn.execute(
            "SELECT id, name, folder_path, protocol, port, username, password, enabled, read_only, created_at "
            "FROM network_shares WHERE id = ?",
            (share_id,),
        ).fetchone()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "folder_path": row[2],
                "protocol": row[3],
                "port": row[4],
                "username": row[5],
                "password": row[6],
                "enabled": bool(row[7]),
                "read_only": bool(row[8]),
                "created_at": row[9],
            }
        return None
    except Exception as e:
        logging.error("Failed to get network share %s: %s", share_id, e)
        return None


def update_network_share(conn: sqlite3.Connection, share_id: str, **kwargs) -> bool:
    valid_fields = {
        "enabled",
        "read_only",
        "password",
        "port",
        "username",
        "name",
        "folder_path",
        "protocol",
    }
    field_sql = {
        "enabled": "enabled = ?",
        "read_only": "read_only = ?",
        "password": "password = ?",
        "port": "port = ?",
        "username": "username = ?",
        "name": "name = ?",
        "folder_path": "folder_path = ?",
        "protocol": "protocol = ?",
    }
    updates = []
    values = []
    for field, value in kwargs.items():
        if field not in valid_fields:
            continue
        if field not in field_sql:
            continue
        updates.append(field_sql[field])
        if field in ("enabled", "read_only"):
            values.append(1 if value else 0)
        else:
            values.append(value)
    if not updates:
        return False
    values.append(share_id)
    try:
        with conn:
            conn.execute(
                format_update_by_id_sql("network_shares", ", ".join(updates)),
                values,
            )
        return True
    except Exception as e:
        logging.error("Failed to update network share %s: %s", share_id, e)
        return False


def delete_network_share(conn: sqlite3.Connection, share_id: str) -> bool:
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM network_shares WHERE id = ?", (share_id,)
            )
            return cursor.rowcount > 0
    except Exception as e:
        logging.error("Failed to delete network share %s: %s", share_id, e)
        return False
