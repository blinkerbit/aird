"""LDAP configuration and synchronization functions."""

import logging
import sqlite3
from datetime import datetime

try:
    from ldap3 import Server, Connection, ALL

    LDAP3_AVAILABLE = True
except ImportError:
    Server = None
    Connection = None
    ALL = None
    LDAP3_AVAILABLE = False

from aird.database.users import create_user, get_all_users, delete_user
from aird.sql_identifiers import format_update_by_id_sql


def create_ldap_config(
    conn: sqlite3.Connection,
    name: str,
    server: str,
    ldap_base_dn: str,
    ldap_member_attributes: str,
    user_template: str,
) -> dict:
    """Create a new LDAP configuration"""
    try:
        created_at = datetime.now().isoformat()

        with conn:
            cursor = conn.execute(
                """INSERT INTO ldap_configs (name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    server,
                    ldap_base_dn,
                    ldap_member_attributes,
                    user_template,
                    created_at,
                ),
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
            "active": True,
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"LDAP configuration '{name}' already exists")


def get_all_ldap_configs(conn: sqlite3.Connection) -> list[dict]:
    """Get all LDAP configurations"""
    try:
        rows = conn.execute(
            "SELECT id, name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at, active FROM ldap_configs ORDER BY created_at DESC, id DESC"
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
                "active": bool(row[7]),
            }
            for row in rows
        ]
    except Exception:
        return []


def get_ldap_config_by_id(conn: sqlite3.Connection, config_id: int) -> dict | None:
    """Get LDAP configuration by ID"""
    try:
        row = conn.execute(
            "SELECT id, name, server, ldap_base_dn, ldap_member_attributes, user_template, created_at, active FROM ldap_configs WHERE id = ?",
            (config_id,),
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
                "active": bool(row[7]),
            }
    except Exception:
        return None


def update_ldap_config(conn: sqlite3.Connection, config_id: int, **kwargs) -> bool:
    """Update LDAP configuration"""
    try:
        valid_fields = [
            "name",
            "server",
            "ldap_base_dn",
            "ldap_member_attributes",
            "user_template",
            "active",
        ]
        updates = []
        values = []

        text_field_sql = {
            "name": "name = ?",
            "server": "server = ?",
            "ldap_base_dn": "ldap_base_dn = ?",
            "ldap_member_attributes": "ldap_member_attributes = ?",
            "user_template": "user_template = ?",
        }
        for field, value in kwargs.items():
            if field not in valid_fields:
                continue
            if field == "active":
                updates.append("active = ?")
                values.append(1 if value else 0)
            elif field in text_field_sql:
                updates.append(text_field_sql[field])
                values.append(value)

        if not updates:
            return False

        values.append(config_id)
        query = format_update_by_id_sql("ldap_configs", ", ".join(updates))

        with conn:
            conn.execute(query, values)
        return True
    except Exception:
        return False


def delete_ldap_config(conn: sqlite3.Connection, config_id: int) -> bool:
    """Delete LDAP configuration"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM ldap_configs WHERE id = ?", (config_id,))
            return cursor.rowcount > 0
    except Exception:
        return False


def log_ldap_sync(
    conn: sqlite3.Connection,
    config_id: int,
    sync_type: str,
    users_found: int,
    users_created: int,
    users_removed: int,
    status: str,
    error_message: str = None,
) -> None:
    """Log LDAP synchronization results"""
    try:
        sync_time = datetime.now().isoformat()
        with conn:
            conn.execute(
                """INSERT INTO ldap_sync_log (config_id, sync_type, users_found, users_created, users_removed, sync_time, status, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    config_id,
                    sync_type,
                    users_found,
                    users_created,
                    users_removed,
                    sync_time,
                    status,
                    error_message,
                ),
            )
    except Exception:
        logging.debug("LDAP sync log insert failed", exc_info=True)


def get_ldap_sync_logs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Get recent LDAP sync logs"""
    try:
        rows = conn.execute(
            """SELECT l.id, l.config_id, c.name, l.sync_type, l.users_found, l.users_created, l.users_removed, 
                      l.sync_time, l.status, l.error_message
               FROM ldap_sync_log l
               JOIN ldap_configs c ON l.config_id = c.id
               ORDER BY l.sync_time DESC
               LIMIT ?""",
            (limit,),
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
                "error_message": row[9],
            }
            for row in rows
        ]
    except Exception:
        return []


def extract_username_from_dn(dn: str, user_template: str) -> str | None:
    """Extract username from LDAP DN using the user template"""
    try:
        if "{username}" in user_template:
            parts = dn.split(",")
            for part in parts:
                if "=" in part:
                    key, value = part.split("=", 1)
                    if key.strip().lower() in ["uid", "cn", "samaccountname"]:
                        return value.strip()
        return None
    except Exception:
        return None


def _collect_usernames_from_ldap_entries(
    entries, member_attr: str, user_template: str
) -> set:
    """Extract usernames from LDAP entries (groupOfNames member attributes)."""
    config_users = set()
    for entry in entries:
        if not hasattr(entry, member_attr):
            continue
        members = getattr(entry, member_attr)
        if not members:
            continue
        for member in members:
            username = extract_username_from_dn(member, user_template)
            if username:
                config_users.add(username)
    return config_users


def _sync_one_ldap_config(conn: sqlite3.Connection, config: dict):
    """Sync one LDAP config. Return (config_users: set, result_dict)."""
    try:
        server = Server(config["server"], get_info=ALL)
        conn_ldap = Connection(server)
        if not conn_ldap.bind():
            return (
                set(),
                {
                    "config_name": config["name"],
                    "status": "error",
                    "message": f"Failed to bind to LDAP server: {config['server']}",
                },
            )
        conn_ldap.search(
            search_base=config["ldap_base_dn"],
            search_filter="(objectClass=groupOfNames)",
            attributes=[config["ldap_member_attributes"]],
        )
        config_users = _collect_usernames_from_ldap_entries(
            conn_ldap.entries,
            config["ldap_member_attributes"],
            config["user_template"],
        )
        log_ldap_sync(
            conn, config["id"], "group_sync", len(config_users), 0, 0, "success"
        )
        return (
            config_users,
            {
                "config_name": config["name"],
                "status": "success",
                "users_found": len(config_users),
            },
        )
    except Exception as e:
        log_ldap_sync(conn, config["id"], "group_sync", 0, 0, 0, "error", str(e))
        return (
            set(),
            {"config_name": config["name"], "status": "error", "message": str(e)},
        )


def _apply_ldap_user_changes(
    conn: sqlite3.Connection, all_ldap_users: set
) -> tuple[int, int]:
    """Create missing users and remove users no longer in LDAP. Return (created, removed)."""
    current_users = get_all_users(conn)
    current_usernames = {u["username"] for u in current_users}
    users_created = 0
    users_removed = 0
    for username in all_ldap_users:
        if username not in current_usernames:
            try:
                create_user(conn, username, "ldap_user", role="user")
                users_created += 1
            except Exception as e:
                print(f"Failed to create user {username}: {e}")
    for user in current_users:
        if user["username"] not in all_ldap_users and user["username"] != "admin":
            try:
                delete_user(conn, user["id"])
                users_removed += 1
            except Exception as e:
                print(f"Failed to remove user {user['username']}: {e}")
    return (users_created, users_removed)


def _collect_ldap_users_from_configs(conn: sqlite3.Connection, active_configs: list):
    """Sync each config and collect all LDAP usernames. Return (all_ldap_users, sync_results)."""
    all_ldap_users = set()
    sync_results = []
    for config in active_configs:
        config_users, result = _sync_one_ldap_config(conn, config)
        all_ldap_users |= config_users
        sync_results.append(result)
    return (all_ldap_users, sync_results)


def _run_ldap_sync(conn: sqlite3.Connection, active_configs: list) -> dict:
    """Collect users from configs and apply DB changes. Return success result dict."""
    all_ldap_users, sync_results = _collect_ldap_users_from_configs(
        conn, active_configs
    )
    users_created, users_removed = _apply_ldap_user_changes(conn, all_ldap_users)
    return {
        "status": "success",
        "total_ldap_users": len(all_ldap_users),
        "users_created": users_created,
        "users_removed": users_removed,
        "config_results": sync_results,
    }


def _get_active_ldap_configs(conn: sqlite3.Connection) -> list:
    """Return list of active LDAP configs."""
    return [c for c in get_all_ldap_configs(conn) if c["active"]]


def _ldap_sync_precondition(conn: sqlite3.Connection) -> tuple[dict | None, list]:
    """Return (early_result_or_None, active_configs). If early_result is set, active_configs may be empty."""
    if not conn:
        return ({"status": "error", "message": "Database not available"}, [])
    active = _get_active_ldap_configs(conn)
    if not active:
        return (
            {"status": "success", "message": "No active LDAP configurations found"},
            [],
        )
    return (None, active)


def sync_ldap_users(conn: sqlite3.Connection) -> dict:
    """Synchronize users from all active LDAP configurations"""
    early_result, active_configs = _ldap_sync_precondition(conn)
    if early_result is not None:
        return early_result
    try:
        return _run_ldap_sync(conn, active_configs)
    except Exception as e:
        return {"status": "error", "message": str(e)}

        print("LDAP sync scheduler started (daily at 2 AM)")
