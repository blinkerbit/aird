"""Feature flags management."""

import sqlite3


def load_feature_flags(conn: sqlite3.Connection) -> dict:
    """Load feature flags from the database."""
    try:
        rows = conn.execute("SELECT key, value FROM feature_flags").fetchall()
        return {k: bool(v) for (k, v) in rows}
    except Exception:
        return {}


def save_feature_flags(conn: sqlite3.Connection, flags: dict) -> None:
    """Save feature flags to the database."""
    try:
        with conn:
            for k, v in flags.items():
                conn.execute(
                    "REPLACE INTO feature_flags (key, value) VALUES (?, ?)",
                    (k, 1 if v else 0),
                )
    except Exception:
        pass


def load_websocket_config(conn: sqlite3.Connection) -> dict:
    """Load WebSocket configuration from SQLite database."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)")
        rows = conn.execute("SELECT key, value FROM websocket_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}


def save_websocket_config(conn: sqlite3.Connection, config: dict) -> None:
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


def is_feature_enabled(key: str, default: bool = False) -> bool:
    """Check if a feature flag is enabled."""
    from aird.database.db import get_db_conn
    from aird.constants import FEATURE_FLAGS
    
    current = FEATURE_FLAGS.copy()
    conn = get_db_conn()
    if conn is not None:
        try:
            persisted = load_feature_flags(conn)
            if persisted:
                for k, v in persisted.items():
                    current[k] = bool(v)
        except Exception:
            pass
    return bool(current.get(key, default))
