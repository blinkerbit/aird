"""Configuration database operations (feature flags, upload, websocket, extensions)."""

import sqlite3


def load_feature_flags(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute("SELECT key, value FROM feature_flags").fetchall()
        return {k: bool(v) for (k, v) in rows}
    except Exception:
        return {}


def save_feature_flags(conn: sqlite3.Connection, flags: dict) -> None:
    try:
        with conn:
            for k, v in flags.items():
                conn.execute(
                    "REPLACE INTO feature_flags (key, value) VALUES (?, ?)",
                    (k, 1 if v else 0),
                )
    except Exception:
        pass


def load_upload_config(conn: sqlite3.Connection) -> dict:
    """Load upload configuration from SQLite database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS upload_config (key TEXT PRIMARY KEY, value INTEGER)"
        )
        rows = conn.execute("SELECT key, value FROM upload_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}


def save_upload_config(conn: sqlite3.Connection, config: dict) -> None:
    """Save upload configuration to SQLite database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS upload_config (key TEXT PRIMARY KEY, value INTEGER)"
        )
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO upload_config (key, value) VALUES (?, ?)",
                    (key, int(value)),
                )
    except Exception:
        pass


def load_allowed_extensions(conn: sqlite3.Connection) -> set:
    """Load allowed upload extensions from database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS upload_allowed_extensions (ext TEXT PRIMARY KEY)"
        )
        rows = conn.execute("SELECT ext FROM upload_allowed_extensions").fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def save_allowed_extensions(conn: sqlite3.Connection, extensions: set) -> None:
    """Replace stored allowed extensions with the given set."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS upload_allowed_extensions (ext TEXT PRIMARY KEY)"
        )
        with conn:
            conn.execute("DELETE FROM upload_allowed_extensions")
            for ext in extensions:
                if ext and isinstance(ext, str) and ext.startswith("."):
                    conn.execute(
                        "INSERT INTO upload_allowed_extensions (ext) VALUES (?)", (ext,)
                    )
    except Exception:
        pass


def load_websocket_config(conn: sqlite3.Connection) -> dict:
    """Load WebSocket configuration from SQLite database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)"
        )
        rows = conn.execute("SELECT key, value FROM websocket_config").fetchall()
        return {k: int(v) for (k, v) in rows}
    except Exception:
        return {}


def save_websocket_config(conn: sqlite3.Connection, config: dict) -> None:
    """Save WebSocket configuration to SQLite database."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS websocket_config (key TEXT PRIMARY KEY, value INTEGER)"
        )
        with conn:
            for key, value in config.items():
                conn.execute(
                    "INSERT OR REPLACE INTO websocket_config (key, value) VALUES (?, ?)",
                    (key, int(value)),
                )
    except Exception:
        pass
