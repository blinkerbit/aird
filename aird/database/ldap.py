"""LDAP configuration and synchronization functions."""

import sqlite3
import logging
import time
import threading
from datetime import datetime
from ldap3 import Server, Connection, ALL


def create_ldap_config(conn: sqlite3.Connection, name: str, server: str, ldap_base_dn: str, 
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


def get_all_ldap_configs(conn: sqlite3.Connection) -> list[dict]:
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


def get_ldap_config_by_id(conn: sqlite3.Connection, config_id: int) -> dict | None:
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


def update_ldap_config(conn: sqlite3.Connection, config_id: int, **kwargs) -> bool:
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


def delete_ldap_config(conn: sqlite3.Connection, config_id: int) -> bool:
    """Delete LDAP configuration"""
    try:
        with conn:
            cursor = conn.execute("DELETE FROM ldap_configs WHERE id = ?", (config_id,))
            return cursor.rowcount > 0
    except Exception:
        return False


def log_ldap_sync(conn: sqlite3.Connection, config_id: int, sync_type: str, users_found: int, 
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


def extract_username_from_dn(dn: str, user_template: str) -> str:
    """Extract username from LDAP DN using the user template"""
    try:
        if '{username}' in user_template:
            parts = dn.split(',')
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    if key.strip() in ['uid', 'cn', 'sAMAccountName']:
                        return value.strip()
        return None
    except Exception:
        return None


def sync_ldap_users(conn: sqlite3.Connection) -> dict:
    """Synchronize users from all active LDAP configurations"""
    from aird.database.users import create_user, get_all_users, delete_user
    
    if not conn:
        return {"status": "error", "message": "Database not available"}
    
    try:
        configs = get_all_ldap_configs(conn)
        active_configs = [c for c in configs if c['active']]
        
        if not active_configs:
            return {"status": "success", "message": "No active LDAP configurations found"}
        
        all_ldap_users = set()
        sync_results = []
        
        for config in active_configs:
            try:
                server = Server(config['server'], get_info=ALL)
                conn_ldap = Connection(server)
                
                if not conn_ldap.bind():
                    sync_results.append({
                        "config_name": config['name'],
                        "status": "error",
                        "message": f"Failed to bind to LDAP server: {config['server']}"
                    })
                    continue
                
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
                                username = extract_username_from_dn(member, config['user_template'])
                                if username:
                                    config_users.add(username)
                                    all_ldap_users.add(username)
                
                sync_results.append({
                    "config_name": config['name'],
                    "status": "success",
                    "users_found": len(config_users)
                })
                
                log_ldap_sync(conn, config['id'], 'group_sync', len(config_users), 0, 0, 'success')
                
            except Exception as e:
                sync_results.append({
                    "config_name": config['name'],
                    "status": "error",
                    "message": str(e)
                })
                log_ldap_sync(conn, config['id'], 'group_sync', 0, 0, 0, 'error', str(e))
        
        # Sync users with the database
        users_created = 0
        users_removed = 0
        
        current_users = get_all_users(conn)
        current_usernames = {user['username'] for user in current_users}
        
        for username in all_ldap_users:
            if username not in current_usernames:
                try:
                    create_user(conn, username, "ldap_user", role='user')
                    users_created += 1
                except Exception as e:
                    print(f"Failed to create user {username}: {e}")
        
        for user in current_users:
            if user['username'] not in all_ldap_users and user['username'] != 'admin':
                try:
                    delete_user(conn, user['id'])
                    users_removed += 1
                except Exception as e:
                    print(f"Failed to remove user {user['username']}: {e}")
        
        return {
            "status": "success",
            "total_ldap_users": len(all_ldap_users),
            "users_created": users_created,
            "users_removed": users_removed,
            "config_results": sync_results
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


def start_ldap_sync_scheduler(conn: sqlite3.Connection) -> None:
    """Start the daily LDAP sync scheduler in a background thread"""
    def sync_worker():
        while True:
            try:
                current_time = datetime.now()
                if current_time.hour == 2 and current_time.minute == 0:
                    print("Starting daily LDAP sync...")
                    sync_result = sync_ldap_users(conn)
                    if sync_result["status"] == "success":
                        print(f"Daily LDAP sync completed: {sync_result.get('total_ldap_users', 0)} users found")
                    else:
                        print(f"Daily LDAP sync failed: {sync_result.get('message', 'Unknown error')}")
                
                time.sleep(60)
            except Exception as e:
                print(f"Error in LDAP sync scheduler: {e}")
                time.sleep(300)
    
    if conn:
        sync_thread = threading.Thread(target=sync_worker, daemon=True)
        sync_thread.start()
        print("LDAP sync scheduler started (daily at 2 AM)")
